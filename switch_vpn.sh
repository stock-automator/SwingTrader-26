#!/bin/bash

# ============================================================
# switch_vpn.sh
# Switch NordVPN country using macOS AppleScript
# ============================================================

COUNTRY="$1"

if [ -z "$COUNTRY" ]; then
    echo "Usage: ./switch_vpn.sh 'Country Name'"
    exit 1
fi

echo "Switching VPN to: $COUNTRY"

osascript <<EOF
tell application "NordVPN"
    activate
end tell

delay 2

tell application "System Events"
    tell process "NordVPN"

        try
            click button "$COUNTRY" of window 1
            delay 5

        on error

            try
                keystroke "$COUNTRY"
                delay 2
                key code 36
                delay 5

            on error

                error "Could not find VPN country button"

            end try

        end try

    end tell
end tell
EOF

if [ $? -eq 0 ]; then

    echo "✓ VPN switch command completed"
    exit 0

else

    echo "✗ VPN switch failed"
    exit 1

fi