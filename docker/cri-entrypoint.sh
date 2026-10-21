#!/bin/bash
set -euo pipefail

# Initialize an empty company database before the upstream entrypoint sees it.
# This prevents upstream demo companies from being loaded on first startup.
python3 - <<'PY'
from pathlib import Path
import secrets
import shutil

# Upstream declares /etc/frepple as a volume. Link to immutable image source
# rather than letting an old copy in that volume shadow a rebuilt UI.
app = Path('/etc/frepple/apps/cri_ui')
app.parent.mkdir(parents=True, exist_ok=True)
if not app.is_symlink():
    if app.exists():
        shutil.rmtree(app)
    app.symlink_to('/opt/cri-pumps/cri_ui', target_is_directory=True)

path = Path('/etc/frepple/djangosettings.py')
text = path.read_text()
marker = '%@mzit!i8b*$zc&6oev96=RANDOMSTRING'
if marker in text:
    path.write_text(text.replace(marker, secrets.token_urlsafe(48)))
PY
frepplectl createdatabase --skip-if-exists
frepplectl migrate --noinput
frepplectl collectstatic --noinput
exec /entrypoint.sh "$@"
