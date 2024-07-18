#!/usr/bin/env bash

DEFAULT_ROOT=/opt/alfred

### Ensure docker-compose is installed -----------------------------------------
function unable-to-install() {
  dialog --keep-tite --msgbox "Unable to install docker-compose." 5 37
  exit 1
}

if ! command -v docker-compose; then
  dialog --keep-tite --yesno \
    "docker-compose is not installed.\n\nWould you like to install it now?" \
    0 0

  if [ $? -eq 1 ]; then
    dialog --keep-tite --msgbox "Please install docker-compose." 5 34
    exit 1
  fi

  if command -v apt-get; then
    if ! apt-get install -y docker-compose; then
      unable-to-install
    fi
  elif command -v dnf; then
    if ! dnf install docker-compose; then
      unable-to-install
    fi
  elif command -v pacman; then
    if ! pacman -Sy docker-compose --noconfirm; then
      unable-to-install
    fi
  else
    unable-to-install
  fi
fi
### ============================================================================

### Prompt for the installation directory --------------------------------------
exec 3>&1
ROOT=$(
  dialog --keep-tite --inputbox \
    "Where would you like to install Alfred?" \
    0 0 "${DEFAULT_ROOT}" 2>&1 1>&3
)

if [ $? -eq 1 ]; then
  exit 1;
fi
exec 3>&-
### ============================================================================

### Ensure the project folder exists and is writeable --------------------------
if [ ! -d "${ROOT}" ]; then
  if [ ! -w "$(dirname "${ROOT}")" ]; then
    dialog --keep-tite --msgbox "Unable to create folder: ${ROOT}" 5 41
    exit 1
  fi

  mkdir -p "${ROOT}"
fi

if [ ! -w "${ROOT}" ]; then
  dialog --keep-tite --msgbox "Unable to write to ${ROOT}" 5 36
  exit 1
fi

cd "${ROOT}"
### ============================================================================

### Download the service files -------------------------------------------------
curl -O "https://github.com/dangle/alfred/blob/main/docker-compose.yml"
curl -O "https://github.com/dangle/alfred/blob/main/alfred.service"
sed -i "s;/opt/alfred/;${ROOT}/;g" alfred.service
### ============================================================================

### Setup the .env file if it does not exist -----------------------------------
if [ ! -f "${ROOT}/.env" ]; then

  ### Get the Discord token for the bot ----------------------------------------
  exec 3>&1
  while [ -z "${TOKEN}" ]; do
    TOKEN=$(dialog --keep-tite --inputbox "Discord Token" 0 0 2>&1 1>&3)

    if [ $? -eq 1 ]; then
      exit 1;
    fi
  done
  exec 3>&-

  echo "DISCORD_TOKEN=${TOKEN}" > .env
  ### ==========================================================================

  ### Get the server ID for the bot --------------------------------------------
  exec 3>&1
  while [ -z "${SERVER_ID}" ]; do
    SERVER_ID=$(dialog --keep-tite --inputbox "Server ID " 0 0 2>&1 1>&3)

    if [ $? -eq 1 -o -z "${SERVER_ID}" ]; then
      dialog --keep-tite --yesno \
        "Are you sure you don't want to set a server ID?\n\n`
        `If you do not set a server ID slash commands will be registered as `
        `global and may take up to an hour to become available." \
        0 0

      if [ $? -eq 0 ]; then
        break
      fi
    fi
  done
  exec 3>&-

  if [ -n "${SERVER_ID}" ]; then
    echo "DISCORD_GUILD_IDS=${SERVER_ID}" >> .env
  fi
  ### ==========================================================================

  ### Get the OpenAI API key for the bot ---------------------------------------
  exec 3>&1
  while [ -z "${OPENAI_API_KEY}" ]; do
    OPENAI_API_KEY=$(
      dialog --keep-tite --inputbox "OpenAI API Key " 0 0 2>&1 1>&3
    )

    if [ $? -eq 1 -o -z "${OPENAI_API_KEY}" ]; then
      dialog --keep-tite --yesno \
        "Are you sure you don't want to set an OpenAI API key?\n\n`
        `If you do not set an OpenAI API key the bot will not be able to `
        `respond conversationally." \
        0 0

      if [ $? -eq 0 ]; then
        break
      fi
    fi
  done
  exec 3>&-

  if [ -n "${OPENAI_API_KEY}" ]; then
    echo "OPENAI_API_KEY=${OPENAI_API_KEY}" >> .env
  fi
  ### ==========================================================================

fi
### ============================================================================

### Install and enable the systemd service -------------------------------------
INIT="$(ps --no-headers -o comm 1)"

if [[ "${INIT}" == "systemd" && -w "/etc/systemd/system/" ]]; then
  dialog --keep-tite --yesno \
    "Would you like to install the systemd service?" \
    0 0

  if [ $? -eq 1 ]; then
    exit 0
  fi

  ln -s alfred.service /etc/systemd/system/

  systemctl daemon-reload
  systemctl enable --now docker.service
  systemctl enable --now alfred.service
else
  docker-compose up
fi
### ============================================================================
