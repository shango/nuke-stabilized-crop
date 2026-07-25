"""Nuke startup script for the StabilizedCrop tool.

Nuke runs this automatically for every directory on the plugin path, so nothing
needs to be added to a shared init.py. It only needs this folder itself to be on
NUKE_PATH.

The relative path is resolved by Nuke against the directory holding this file,
and nuke.pluginAddPath() also puts it on sys.path, which is what makes
`import stabilized_crop` work for the node's button callbacks.

The module lives in ./python rather than next to this file on purpose: calling
pluginAddPath on this directory would re-execute this init.py.
"""

import nuke

nuke.pluginAddPath('./python')
