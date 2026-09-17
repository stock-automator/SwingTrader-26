#!/usr/bin/env python3
"""Launcher: `streamlit run run_ui.py`. Thin entrypoint so `streamlit run`
has a root-level target while the actual app lives in src/ui/app.py."""

from src.ui.app import main

if __name__ == "__main__":
    main()
