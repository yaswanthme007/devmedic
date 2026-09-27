#!/usr/bin/env bash
# Install devmedic from this checkout.
#   ./install.sh          -> uses pipx if available (recommended), otherwise
#                            links ~/.local/bin/devmedic to this folder (dev mode)
#   ./install.sh --dev    -> always use dev mode (edits take effect immediately)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" != "--dev" ]] && command -v pipx >/dev/null 2>&1; then
  pipx install --force "$DIR"
  exit 0
fi

python3 -c "import psutil, rich" 2>/dev/null || {
  echo "Missing dependencies. On Ubuntu/Debian run:"
  echo "  sudo apt install python3-psutil python3-rich"
  echo "or install pipx (sudo apt install pipx) and re-run this script."
  exit 1
}
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/devmedic" <<WRAP
#!/usr/bin/env bash
PYTHONPATH="$DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m devmedic "\$@"
WRAP
chmod +x "$HOME/.local/bin/devmedic"
echo "✔ Installed (dev mode): $HOME/.local/bin/devmedic → $DIR"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "⚠ Add ~/.local/bin to your PATH:  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
esac
