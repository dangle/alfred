"""A chat client that manages history."""

from __future__ import annotations

import json
import typing
from collections import defaultdict
from typing import Annotated

import openai
import structlog
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCall,
    ChatCompletionMessageToolCallParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_message_tool_call_param import Function

from alfred.chat.constants import (
    NO_RESPONSE,
    NO_RESPONSE_SYSTEM_MESSAGE,
    RETRY_BAD_RESPONSES,
    TOOL_SYSTEM_MESSAGE,
)
from alfred.chat.context import MessageApplicationContext
from alfred.chat.enum import ChatGPTModels, MessageRole, ResponseType, ToolParamType
from alfred.chat.tools import get_tools
from alfred.core import fields
from alfred.util.autofields import AutoFields
from alfred.util.lock import Locked

if typing.TYPE_CHECKING:
    from typing import Any, Literal

    from discord import Message

    from alfred.chat.tools import Tool
    from alfred.core import models


_log: structlog.stdlib.BoundLogger = structlog.get_logger()


class ChatClient(AutoFields):
    """A chat client that manages history."""

    #: An asynchronous OpenAI client.
    ai: openai.AsyncOpenAI

    #: The chat service model to use when responding to users.
    #: Defaults to GPT-4o.
    model: Annotated[
        str,
        fields.ConfigField[str](namespace="alfred.openai", env="CHATGPT_MODEL"),
    ] = ChatGPTModels.GPT_4O

    #: A value between 0 and 1 describing how creative the chat service should be when answering.
    #: Higher values are more creative.
    temperature: Annotated[
        float,
        fields.BoundedConfigField[float](namespace="alfred.openai", lower_bound=0, upper_bound=1),
    ] = 0.2

    def __init__(self, staff: models.Staff) -> None:
        self._staff: models.Staff = staff
        self._tools: dict[str, Tool] = get_tools(self._staff.application_commands)
        self._history: dict[int, Locked[list[ChatCompletionMessageParam]]] = defaultdict(
            lambda: Locked([]),
        )

        structlog.contextvars.bind_contextvars(tools=list(self._tools))

    async def update(
        self,
        message: Message,
        *,
        must_respond: bool = True,
        allow_implicit: bool = False,
    ) -> str | None:
        """Update the chat client with the given message and get a response from the chat service.

        Parameters
        ----------
        message : Message
            The message sent in a channel that the bot watches.
        must_respond : bool
            If False, the bot may choose not to respond if it thinks it appropriate.
        allow_implicit : bool
            Whether or not the bot should be listening to chat without explicit mentions of the
            bot's name.

        Returns
        -------
        str | None
            The response if the chat service returned one; otherwise None.

        """
        if message.author.id == self._staff.application_id:
            await _log.adebug("Bot is the author of the message.", message=message)
            return None

        author: str = self._staff.get_user_name(message.author)

        async with self._history[message.channel.id] as history:
            history.append(
                ChatCompletionUserMessageParam(
                    role=MessageRole.User,
                    content=message.content,
                    name=author,
                ),
            )

            if message.author.bot:
                await _log.adebug("Another bot is the author of the message.", message=message)
                return None

            if not must_respond and not allow_implicit:
                return None

            response: str | ResponseType = await self._get_chat_response_to_history(
                message,
                must_respond=must_respond,
            )

            match response:
                case ResponseType.BadResponse:
                    raise RuntimeError  # TODO: Real exception
                case ResponseType.NoResponse:
                    await _log.adebug(
                        f"Skipping response to message from {author}.",
                        response=response,
                    )
                    return None
                case ResponseType.ToolCall:
                    return None
                case _:
                    return response

    async def _get_system_message(self, message: Message) -> str:
        guild_id: int | None = (
            message.channel.guild.id if hasattr(message.channel, "guild") else None
        )
        identity: models.Identity = await self._staff.get_identity(guild_id)
        return identity.description

    async def _get_chat_response_to_history(
        self,
        message: Message,
        *,
        must_respond: bool,
    ) -> str | ResponseType:
        """Send a message to the chat service with historical context and return the response.

        If `must_respond` is `False` this will prepend a system message that allows the bot to
        determine if it believes it is being addressed. If it is not being addressed, this will not
        return a response.

        Parameters
        ----------
        message : Message
            The message to which the bot would reply.
        must_respond : bool
            Determines if the bot may ignore the message.

        Returns
        -------
        str | _ResponseType
            Returns the response from the chat service if the bot believes that it is being
            addressed.
            If the bot does not respond directly to this call it returns a `_ResponseType` object
            detailing why.

        """
        try:
            assistant_message = await self._get_assistant_message(
                message,
                must_respond=must_respond,
            )
        except openai.OpenAIError as e:
            await _log.aerror("An error occurred while querying the chat service.", exc_info=e)
            return ResponseType.BadResponse

        if assistant_message == NO_RESPONSE:
            return ResponseType.NoResponse

        if assistant_message:
            self._history[message.channel.id].stored_object.append(
                ChatCompletionAssistantMessageParam(
                    role=MessageRole.Assistant,
                    content=message.content,
                ),
            )
            return assistant_message

        await _log.aerror("All responses from the chat service started with the prompt unmodified.")
        return ResponseType.BadResponse

    async def _get_assistant_message(
        self,
        message: Message,
        *,
        must_respond: bool,
    ) -> str | None | Literal[ResponseType.ToolCall]:
        """Send a message to the chat service with historical context and return the response.

        If the response is the same as the message prompt, this will retry the message with each
        request asking for more choices.

        If `must_respond` is `False` this will prepend a system message that allows the bot to
        determine if it believes it is being addressed. If it is not being addressed, this will not
        return a response.

        Parameters
        ----------
        message : Message
            The message to which the bot would reply.
        must_respond : bool
            Determines if the bot may ignore the message.

        Returns
        -------
        str | None | Literal[_ResponseType.ToolCall]
            Returns the response from the chat service if the bot believes that it is being
            addressed.
            If the bot would instead call a tool, this returns `_ResponseType.ToolCall`.

        """
        tools = [tool.tool for tool in self._tools.values()] or openai.NOT_GIVEN
        chat_context = await self._get_chat_context(message, must_respond=must_respond)

        for n in range(1, RETRY_BAD_RESPONSES + 1):
            response: ChatCompletion = await self._call_chat(
                message,
                tools=tools,
                messages=chat_context,
                n=n,
            )
            await _log.adebug(
                "Got response from chat service.",
                response=response,
                message=message,
                n=n,
            )

            if response.choices[0].message.tool_calls:
                await self._call_tool(message, response.choices[0].message)
                return ResponseType.ToolCall

            for choice in response.choices:
                if choice.message.content and not choice.message.content.startswith(
                    message.content,
                ):
                    return choice.message.content

        return None

    async def _call_tool(
        self,
        message: Message,
        response_message: ChatCompletionMessage,
    ) -> None:
        """Call a tool requested by the chat service and handle responses.

        This calls the tool and then calls the chat service again with the output of the tool.
        Any responses sent by the tool are delayed and updated with the response from the chat
        service.

        Parameters
        ----------
        message : Message
            The message containing the request that triggered the tool use.
        response_message : ChatCompletionMessage
            The response message that contains the tool to call.

        """
        response_message.tool_calls = typing.cast(
            list[ChatCompletionMessageToolCall],
            response_message.tool_calls,
        )
        self._history[message.channel.id].stored_object.append(
            self._get_tool_assistant_message(message, response_message),
        )
        tool_call = response_message.tool_calls[0]
        function = tool_call.function
        command = self._tools[function.name].command

        async with await MessageApplicationContext.new(
            self._staff,
            message,
            delayed_send=True,
        ) as ctx:
            await _log.ainfo("Calling tool.", tool=function.name, tool_call_id=tool_call.id)

            try:
                await command(ctx=ctx, **json.loads(function.arguments))
                content = json.dumps([r.serializable() for r in ctx.responses])
            except Exception as e:
                await _log.aerror(
                    "An error occurred while calling a tool.",
                    exc_info=e,
                    tool_call_id=tool_call.id,
                    function_name=function.name,
                )
                content = str(e)

            self._history[message.channel.id].stored_object.append(
                ChatCompletionToolMessageParam(
                    tool_call_id=tool_call.id,
                    role=MessageRole.Tool,
                    content=content,
                ),
            )

            response_message = (
                (
                    await self._call_chat(
                        message,
                        messages=await self._get_chat_context(message, must_respond=False),
                    )
                )
                .choices[0]
                .message
            )
            self._history[message.channel.id].stored_object.append(
                ChatCompletionAssistantMessageParam(
                    role=MessageRole.Assistant,
                    content=response_message.content,
                ),
            )

            if len(ctx.responses) == 1 and response_message.content != message.content:
                ctx.responses[0].content = response_message.content

    def _get_tool_assistant_message(
        self,
        message: Message,
        response_message: ChatCompletionMessage,
    ) -> ChatCompletionAssistantMessageParam:
        """Create a message that calls a tool.

        Parameters
        ----------
        message : Message
            The message that triggered the tool call.
        response_message : ChatCompletionMessage
            The response from the last call to the chat service.

        Returns
        -------
        ChatCompletionAssistantMessageParam
            A chat parameter to add to the context when calling the chat service.

        """
        return ChatCompletionAssistantMessageParam(
            role=MessageRole.Assistant,
            content=message.content,
            tool_calls=(
                [
                    ChatCompletionMessageToolCallParam(
                        id=tc.id,
                        type=ToolParamType.Function,
                        function=Function(
                            name=tc.function.name,
                            arguments=tc.function.arguments,
                        ),
                    )
                    for tc in response_message.tool_calls
                ]
                if response_message.tool_calls
                else []
            ),
        )

    async def _call_chat(self, message: Message, **kwargs: Any) -> ChatCompletion:
        """Call the chat service.

        Parameters
        ----------
        message : Message
            The message that triggered the call to the chat service.
        kwargs : dict[str, Any]
            All keywords to pass to the chat service.

        Returns
        -------
        ChatCompletionMessage
            The response message from the chat service.

        """
        if "model" not in kwargs:
            kwargs["model"] = self.model

        if "temperature" not in kwargs:
            kwargs["temperature"] = self.temperature

        if "user" not in kwargs:
            kwargs["user"] = self._staff.get_user_name(message.author)

        response: ChatCompletion = await self.ai.chat.completions.create(**kwargs)

        structlog.contextvars.bind_contextvars(
            chat_usage=(
                {
                    "completion_tokens": response.usage.completion_tokens,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else None
            ),
            history_length=len(self._history),
        )

        return response

    async def _get_chat_context(
        self,
        message: Message,
        *,
        must_respond: bool,
    ) -> list[ChatCompletionMessageParam]:
        """Return a list of historical chat messages to send to the chat service.

        If `must_respond` is `False`, the history will prepend a system message telling the bot to
        respond a specific way if it thinks that it is not being addressed.

        If `config.chatgpt_system_message` has been configured, the history will prepend a system
        with the contents of that value.

        If `must_respond` is `False` and `config.chatgpt_system_message` has been configured, the
        history will have a *single* system message prepended that is the two concatenated together.

        Parameters
        ----------
        message : Message
            The currently active `Message` to which the bot is replying.
        must_respond : bool
            If `must_respond` is `True`, no system message will be added that tells the bot that to
            determine if it is being addressed.

        Returns
        -------
        list[ChatCompletionMessageParam]
            A list of historical messages for the channel along with an optionally prepended system
            message.

        """
        system_message: str = await self._get_system_message(message)
        messages: list[ChatCompletionMessageParam] = self._history[
            message.channel.id
        ].stored_object.copy()

        if not must_respond or system_message:
            messages.insert(
                0,
                ChatCompletionSystemMessageParam(
                    role=MessageRole.System,
                    content=(
                        f"{TOOL_SYSTEM_MESSAGE}"
                        f"{"" if must_respond else NO_RESPONSE_SYSTEM_MESSAGE}"
                        f"{system_message}"
                    ),
                ),
            )

        return messages
