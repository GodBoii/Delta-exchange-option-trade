"""Encrypted laptop backup of Supabase and Convex, excluding old chart images."""

import hashlib
import io
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]

REMOTE = r'''
import hashlib, io, json, os, subprocess, sys, urllib.parse, zipfile
inspection = json.loads(subprocess.check_output(['docker','inspect','bitcoin-agent']))[0]
environment = dict(item.split('=',1) for item in inspection['Config']['Env'] if '=' in item)
database = urllib.parse.urlsplit(environment['SUPABASE_DB_URL'].replace('postgresql+psycopg://','postgresql://'))
dump_env = {**os.environ, 'PGPASSWORD': urllib.parse.unquote(database.password)}
command = ['docker','run','--rm','-e','PGPASSWORD','postgres:17-alpine','pg_dump',
           '--host',database.hostname,'--port',str(database.port or 5432),
           '--username',urllib.parse.unquote(database.username),'--dbname',database.path.lstrip('/'),
           '--format=custom','--schema=public','--schema=ai','--schema=auth','--schema=storage','--schema=vault']
dump = subprocess.run(command, env=dump_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if dump.returncode: raise RuntimeError('pg_dump failed: '+dump.stderr.decode())
inventory_code = """
import os,json,psycopg
from psycopg import sql
from psycopg.rows import dict_row
with psycopg.connect(os.environ['SUPABASE_DB_URL'],row_factory=dict_row) as c:
 c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 tables=c.execute("select table_schema,table_name from information_schema.tables "
  "where table_schema in ('public','ai','auth','storage','vault') and table_type='BASE TABLE' order by 1,2").fetchall()
 result={}
 for t in tables:
  name=t['table_schema']+'.'+t['table_name']
  result[name]=c.execute(
   sql.SQL('select to_jsonb(t) as record from {}.{} t').format(sql.Identifier(t['table_schema']),
    sql.Identifier(t['table_name']))).fetchall()
 credentials={}
 for r in c.execute('select user_id from public.exchange_connections').fetchall():
  credentials[str(r['user_id'])]=c.execute(
   'select to_jsonb(t) as record from public.get_delta_credentials(%s) t',(r['user_id'],)).fetchall()
 print(json.dumps({'tables':result,'credentials':credentials},default=str))
"""
records_raw = subprocess.check_output(
 ['docker','exec','-i','bitcoin-agent','python','-'], input=inventory_code.encode())
records = json.loads(records_raw)
archive = io.BytesIO()
manifest = {'tableCounts': {k:len(v) for k,v in records['tables'].items()}, 'files': {}, 'chartsExcluded': True}
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 def put(name, content):
  z.writestr(name,content)
  manifest['files'][name]={'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
 put('supabase.dump',dump.stdout)
 put('records.json',records_raw)
 trading=json.loads(subprocess.check_output(['docker','inspect','Delta-exchange']))[0]
 workdir=trading['Config']['Labels']['com.docker.compose.project.working_dir']
 for name in ['.env.local','backend/.env','docker-compose.yml','docker-compose.tunnel.yml']:
  path=os.path.join(workdir,name)
  if os.path.isfile(path):
   with open(path,'rb') as source: put('deployment/'+name,source.read())
 put('deployment/commit.txt',subprocess.check_output(['git','rev-parse','HEAD'],cwd=workdir))
 put('deployment/source.tar',subprocess.check_output(['git','archive','HEAD'],cwd=workdir))
 put('deployment/working.patch',subprocess.check_output(['git','diff','--binary','HEAD'],cwd=workdir))
 for name in subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=workdir,text=True).splitlines():
  path=os.path.join(workdir,name)
  if os.path.isfile(path):
   with open(path,'rb') as source: put('deployment/untracked/'+name,source.read())
 z.writestr('manifest.json',json.dumps(manifest,indent=2))
sys.stdout.buffer.write(archive.getvalue())
'''


def verify_archive(raw: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if archive.testzip() is not None:
            raise ValueError("Backup ZIP checksum failure")
        manifest = json.loads(archive.read("manifest.json"))
        for name, expected in manifest["files"].items():
            content = archive.read(name)
            if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
                raise ValueError(f"Backup verification failed: {name}")
    return manifest


def main() -> None:
    directory = ROOT / "data" / "backups" / datetime.now(UTC).strftime("redesign-%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=False)
    key = Fernet.generate_key()
    (directory / "recovery.key").write_bytes(key)
    cipher = Fernet(key)
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "ubuntu-server", "python3 -"],
        input=REMOTE.encode(),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    manifest = verify_archive(result.stdout)
    backup_path = directory / "supabase.zip.fernet"
    backup_path.write_bytes(cipher.encrypt(result.stdout))
    verify_archive(cipher.decrypt(backup_path.read_bytes()))
    convex_path = directory / "convex.zip"
    subprocess.run(
        ["cmd", "/c", "npx", "convex", "export", "--deployment", "knowing-horse-0", "--path", str(convex_path)],
        cwd=ROOT,
        check=True,
    )
    convex_raw = convex_path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(convex_raw)) as archive:
        if archive.testzip() is not None:
            raise ValueError("Convex ZIP checksum failure")
        convex_files = archive.namelist()
    encrypted_convex = directory / "convex.zip.fernet"
    encrypted_convex.write_bytes(cipher.encrypt(convex_raw))
    if cipher.decrypt(encrypted_convex.read_bytes()) != convex_raw:
        raise ValueError("Convex encrypted backup mismatch")
    convex_path.unlink()
    manifest["convexFiles"] = convex_files
    manifest["archives"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (backup_path, encrypted_convex)}
    (directory / "verification.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"backup": str(directory), "tableCounts": manifest["tableCounts"], "verified": True}))


if __name__ == "__main__":
    main()
