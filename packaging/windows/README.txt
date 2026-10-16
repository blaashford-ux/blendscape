Blendscape for Windows
======================

First time:   double-click  Install.bat
              (needs Python 3.10 to 3.13; the setup tells you if it is missing)

Every time:   double-click  Start Blendscape.bat
              A browser tab opens at http://127.0.0.1:8765. Keep the black window open while you use it.

Your projects and rendered mixes are kept in  C:\Users\<you>\.local\share\blendscape
(Settings -> "This computer" in the web page lets you also save a copy of each mix in a folder you choose.)

If previews or renders are unavailable, the Rubber Band program was not found: put its location in
Settings -> "This computer" -> Rubber Band program. This download includes a copy in the tools folder when
the build could fetch one.

Something wrong?   Open a PowerShell window in this folder and run:   .venv\Scripts\blendscape.exe doctor

Blendscape is free software (GPL-3.0-or-later): see LICENSE.txt.
Source and issues: https://github.com/blaashford-ux/blendscape
