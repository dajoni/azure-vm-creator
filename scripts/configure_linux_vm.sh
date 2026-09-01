#!/usr/bin/env bash
set -Eeuo pipefail

admin_username=""
desktop_password=""

for arg in "$@"; do
  case "$arg" in
    AdminUsername=*)
      admin_username="${arg#AdminUsername=}"
      ;;
    DesktopPassword=*)
      desktop_password="${arg#DesktopPassword=}"
      ;;
  esac
done

if [[ -z "$admin_username" ]]; then
  echo "AdminUsername parameter is required." >&2
  exit 2
fi

if [[ -z "$desktop_password" ]]; then
  echo "DesktopPassword parameter is required." >&2
  exit 2
fi

if [[ "$EUID" -ne 0 ]]; then
  exec sudo -E bash "$0" "$@"
fi

if ! id "$admin_username" >/dev/null 2>&1; then
  echo "User $admin_username does not exist." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive

user_home="$(getent passwd "$admin_username" | cut -d: -f6)"
user_group="$(id -gn "$admin_username")"
install_root="/var/lib/azure-vm-creator"
mkdir -p "$install_root"

echo "Updating apt metadata..."
apt-get update

echo "Installing base tools for agentic host work..."
apt-get install -y \
  ca-certificates \
  curl \
  wget \
  gnupg \
  git \
  tmux \
  ufw \
  jq \
  unzip \
  zip \
  ripgrep \
  fd-find \
  htop \
  ncdu \
  build-essential \
  python3 \
  python3-pip \
  python3-venv \
  pipx \
  nodejs \
  npm

mkdir -p "$user_home/.local/bin"
if command -v fdfind >/dev/null 2>&1 && [[ ! -e "$user_home/.local/bin/fd" ]]; then
  ln -s /usr/bin/fdfind "$user_home/.local/bin/fd"
fi
chown -R "$admin_username:$user_group" "$user_home/.local"

echo "Installing Docker from Docker's official Ubuntu apt repository..."
for package in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  apt-get remove -y "$package" >/dev/null 2>&1 || true
done

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
ubuntu_codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
if [[ -z "$ubuntu_codename" ]]; then
  echo "Unable to determine Ubuntu codename for Docker apt repository." >&2
  exit 2
fi

cat >/etc/apt/sources.list.d/docker.sources <<DOCKER_SOURCES
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${ubuntu_codename}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
DOCKER_SOURCES

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker "$admin_username"
systemctl enable --now docker

echo "Installing lightweight XFCE desktop and xrdp..."
apt-get install -y xubuntu-desktop-minimal xrdp xorgxrdp dbus-x11 firefox
usermod -aG ssl-cert xrdp || true
printf '%s:%s\n' "$admin_username" "$desktop_password" | chpasswd
printf 'startxfce4\n' >"$user_home/.xsession"
chown "$admin_username:$user_group" "$user_home/.xsession"
chmod 0644 "$user_home/.xsession"

mkdir -p "$user_home/Desktop"
cat >"$user_home/Desktop/ChatGPT.desktop" <<'CHATGPT_DESKTOP'
[Desktop Entry]
Type=Application
Name=ChatGPT
Exec=firefox https://chatgpt.com
Terminal=false
CHATGPT_DESKTOP
cat >"$user_home/Desktop/Claude.desktop" <<'CLAUDE_DESKTOP'
[Desktop Entry]
Type=Application
Name=Claude
Exec=firefox https://claude.ai
Terminal=false
CLAUDE_DESKTOP
chmod 0755 "$user_home/Desktop/ChatGPT.desktop" "$user_home/Desktop/Claude.desktop"
chown -R "$admin_username:$user_group" "$user_home/Desktop"

systemctl enable --now xrdp

echo "Configuring UFW inside the guest..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 3389/tcp
ufw --force enable

touch "$install_root/configure-linux.done"

echo "Docker status: $(systemctl is-active docker)"
echo "xrdp status: $(systemctl is-active xrdp)"
ufw status verbose
echo "Linux configuration complete."
echo "Note: Docker-published ports can bypass UFW unless Docker networking is controlled separately."
