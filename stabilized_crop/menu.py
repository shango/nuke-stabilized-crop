"""Nuke menu registration for the StabilizedCrop tool.

Nuke runs this automatically for every directory on the plugin path, after
init.py has run. Edit the two settings below to place the tool in your studio's
menu.

MENU_PATH may be nested with slashes, for example "MyStudio/Roto".
SHORTCUT is a Nuke shortcut string such as "ctrl+alt+s", or "" for none.
"""

import stabilized_crop

MENU_PATH = "Convert"
SHORTCUT = ""

stabilized_crop.register_menu(MENU_PATH, SHORTCUT)
