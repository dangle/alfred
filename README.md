# Alfred

Alfred is an extensible Discord bot that can use ChatGPT to respond conversationally and run commands on behalf of the server users.

## Installation

### Prerequisites

#### Get Required Values
1. Create a [Discord App](https://discord.com/developers/docs/quick-start/getting-started) and get the token.

  > [!IMPORTANT]
  > For conversational chat support you will need to give the priviledged intents for messages and presence.

#### Get Optional Values
1. Get your [Discord server ID](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID)

  > [!NOTE]
  > Not supplying this will cause commands to be registered as global and they may take up to an hour to become available.

2. Get an [OpenAI API key](https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key)

  > [!WARNING]
  > Not supplying this will prevent Alfred from responding conversationally and only slash commands will be available.

### Guided Installation Script

The easiest way to install the bot is to run the guided installation script.

```sh
curl -sSL https://raw.githubusercontent.com/dangle/alfred/main/install.sh | sudo bash
```

  > [!WARNING]
  > Always verify the contents of the script before running any code from the internet.

### Manually Install Using Docker Compose

1. Create a folder to store your configuration.

```sh
mkdir -p /opt/alfred
cd /opt/alfred
```

2. Download the `docker-compose.yml` file from the repository.

```sh
curl -O https://raw.githubusercontent.com/dangle/alfred/main/docker-compose.yml
```

3. Create a `.env` file with the following variables:

```sh filename=".env"
DISCORD_TOKEN="discord token"
DISCORD_GUILD_IDS="Discord server ID"
OPENAI_API_KEY"OpenAI API key"
```

4. Start the service

```sh
docker-compose up
```

## Contributing

1. [Fork](https://github.com/dangle/alfred/fork) the repository
2. Clone your fork locally
3. Use the [devcontainer](https://containers.dev/).
    1. If you use [VS Code](https://code.visualstudio.com/) it will prompt you to open the project in the devcontainer once you open the folder.
    2. If you prefer not to use VS Code, you can use the [devcontainer-cli](https://github.com/devcontainers/cli) to run the devcontainer.
4. Verify your changes using `pdm start`.
5. Once you are ready to submit a [pull request](https://github.com/dangle/alfred/compare), run `pdm check` to ensure your changes can be merged successfully.
