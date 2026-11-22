#!/bin/zsh
# Prompt for the TikTok client secret (hidden) and write it to .env. Never prints it.
cd "${0:A:h}/.." || exit 1
read -rs "s?TikTok client secret (hidden, press Return): "
echo
s=$(printf %s "$s" | tr -d '[:space:]')
n=${#s}
if [ $n -ge 20 ] && [ $n -le 64 ]; then
  sed -i '' "s|^TIKTOK_CLIENT_SECRET=.*|TIKTOK_CLIENT_SECRET=$s|" .env && echo "saved ($n chars)"
else
  echo "got $n chars, not saved"
fi
