import io
import shutil
import sys
import zipfile

JAR = sys.argv[2] if len(sys.argv) > 2 else "MasterClient_license.jar"
if len(sys.argv) < 2:
    raise SystemExit("Użycie: python set_api.py https://twoja-usluga.onrender.com [MasterClient_license.jar]")
url = sys.argv[1].rstrip("/")
if not url.endswith("/api/heartbeat"):
    url += "/api/heartbeat"
if not url.startswith("http"):
    raise SystemExit("Adres musi zaczynać się od http:// lub https://")

NESTED = "mefa-client-1_21_4.jar"
outer = zipfile.ZipFile(JAR)
inner_zip = zipfile.ZipFile(io.BytesIO(outer.read(NESTED)))
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zo:
    for it in inner_zip.infolist():
        data = url.encode() if it.filename == "moon-api.txt" else inner_zip.read(it.filename)
        zo.writestr(it, data)
tmp = JAR + ".tmp"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
    for it in outer.infolist():
        if it.filename == NESTED:
            data = buf.getvalue()
        elif it.filename == "moon-api.txt":
            data = url.encode()
        else:
            data = outer.read(it.filename)
        zo.writestr(it, data)
outer.close()
shutil.move(tmp, JAR)
print("Ustawiono:", url)
