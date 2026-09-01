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

if [[ -z "$admin_username" && $# -ge 1 ]]; then
  admin_username="$1"
fi

if [[ -z "$desktop_password" && $# -ge 2 ]]; then
  desktop_password="$2"
fi

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
dpkg_arch="$(dpkg --print-architecture)"
install_root="/var/lib/azure-vm-creator"
mkdir -p "$install_root"
tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

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

echo "Installing Google Chrome from Google's official Debian package..."
if [[ "$dpkg_arch" == "amd64" ]]; then
  chrome_deb="$tmp_dir/google-chrome-stable_current_amd64.deb"
  curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o "$chrome_deb"
  apt-get install -y "$chrome_deb"
else
  echo "Skipping Google Chrome: Google's Linux .deb installer is not available for architecture $dpkg_arch." >&2
fi

echo "Installing Claude Desktop from Anthropic's official apt repository..."
curl -fsSLo /usr/share/keyrings/claude-desktop-archive-keyring.asc https://downloads.claude.ai/claude-desktop/key.asc
claude_key_fingerprint="$(gpg --show-keys --with-colons /usr/share/keyrings/claude-desktop-archive-keyring.asc | awk -F: '$1 == "fpr" { print $10; exit }')"
if [[ "$claude_key_fingerprint" != "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE" ]]; then
  echo "Unexpected Claude Desktop apt signing key fingerprint: ${claude_key_fingerprint:-unavailable}" >&2
  exit 2
fi
cat >/etc/apt/sources.list.d/claude-desktop.list <<'CLAUDE_SOURCES'
deb [signed-by=/usr/share/keyrings/claude-desktop-archive-keyring.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main
CLAUDE_SOURCES
apt-get update
apt-get install -y claude-desktop

echo "Installing ChatGPT Desktop for Linux from OpenAI's official package..."
case "$dpkg_arch" in
  amd64)
    chatgpt_deb="$tmp_dir/chatgpt_amd64.deb"
    curl -fsSL https://persistent.oaistatic.com/codex-app-prod/linux/deb/latest/chatgpt_amd64.deb -o "$chatgpt_deb"
    ;;
  arm64)
    chatgpt_deb="$tmp_dir/chatgpt_arm64.deb"
    curl -fsSL https://persistent.oaistatic.com/codex-app-prod/linux/deb/latest/chatgpt_arm64.deb -o "$chatgpt_deb"
    ;;
  *)
    echo "Unsupported architecture for ChatGPT Desktop: $dpkg_arch" >&2
    exit 2
    ;;
esac
apt-get install -y "$chatgpt_deb"

echo "Applying all available apt package upgrades..."
apt-get update
apt-get upgrade -y

echo "Creating desktop launchers for browser and AI apps..."
mkdir -p "$user_home/Desktop"

cat >"$user_home/Desktop/Google Chrome.desktop" <<'CHROME_DESKTOP'
[Desktop Entry]
Type=Application
Name=Google Chrome
Exec=google-chrome
Icon=google-chrome
Terminal=false
Categories=Network;WebBrowser;
CHROME_DESKTOP

cat >"$user_home/Desktop/Firefox.desktop" <<'FIREFOX_DESKTOP'
[Desktop Entry]
Type=Application
Name=Firefox
Exec=firefox
Icon=firefox
Terminal=false
Categories=Network;WebBrowser;
FIREFOX_DESKTOP

cat >"$user_home/Desktop/ChatGPT.desktop" <<'CHATGPT_DESKTOP'
[Desktop Entry]
Type=Application
Name=ChatGPT
Exec=chatgpt
Icon=chatgpt
Terminal=false
Categories=Office;Development;
CHATGPT_DESKTOP

cat >"$user_home/Desktop/Claude.desktop" <<'CLAUDE_DESKTOP'
[Desktop Entry]
Type=Application
Name=Claude
Exec=claude-desktop
Icon=claude-desktop
Terminal=false
Categories=Office;Development;
CLAUDE_DESKTOP

chmod 0755 "$user_home/Desktop/Google Chrome.desktop" \
  "$user_home/Desktop/Firefox.desktop" \
  "$user_home/Desktop/ChatGPT.desktop" \
  "$user_home/Desktop/Claude.desktop"
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
