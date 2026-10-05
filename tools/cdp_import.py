#!/usr/bin/env python3
"""Measure the import's file path for real: pick a file in the browser, read the panel.

The reported symptom ("you cannot upload a file in the import") lived in the interface,
so it has to be measured in the interface. An input's files can only be set over CDP
(DOM.setFileInputFiles) -- the same path a real file picker takes, i.e. a real change
event and not a made-up one.

    python3 tools/cdp_import.py [url] [file]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp_page import open_page  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788/#import"
FILE = sys.argv[2] if len(sys.argv) > 2 else "/tmp/bokning.md"

READ = """(() => {
  const input = document.getElementById('impFiles');
  const label = document.getElementById('impFilesNote');
  const button = document.getElementById('impParse');
  return {nodes_with_id: document.querySelectorAll('[id="impFiles"]').length,
          first_is: input ? input.tagName : '(missing)',
          label: label ? label.textContent.trim() : '(missing)',
          files_in_js: (typeof IMP !== 'undefined' && IMP.files) ? IMP.files.length : -1,
          button_disabled: button ? button.disabled : null};
})()"""

page = open_page(URL)
try:
    print("panel loaded:", page.wait_for("typeof STATE !== 'undefined' && !!STATE && !!STATE.jira"))
    page.js("show('import')")
    print("BEFORE picking:", page.js(READ))
    page.set_file("#impFiles", FILE)
    print("AFTER picking: ", page.js(READ))
finally:
    page.close()
    if page.chrome:
        page.chrome.terminate()
