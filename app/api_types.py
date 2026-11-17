"""Generate React types from FastAPI without a server, database or credentials."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from app.main import create_app

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'frontend/src/api-schema.d.ts'


def schema_text():
    return json.dumps(create_app().openapi(),sort_keys=True,separators=(',',':'))


def schema_header():
    return '// API schema SHA256: '+sha256(schema_text().encode()).hexdigest()


def main():
    with TemporaryDirectory() as directory:
        source=Path(directory)/'openapi.json'
        source.write_text(schema_text(),encoding='utf-8')
        subprocess.run(['node',str(ROOT/'frontend/node_modules/openapi-typescript/bin/cli.js'),
                        str(source),'--output',str(OUTPUT)],check=True,cwd=ROOT)
    OUTPUT.write_text(schema_header()+'\n'+OUTPUT.read_text(encoding='utf-8'),encoding='utf-8')
    from app.contracts_v2 import ComparisonWorkspace
    fixture=ComparisonWorkspace.model_validate_json((ROOT/'tests/fixtures/v2/workspace.json').read_text())
    target=ROOT/'frontend/src/fixtures/workspace.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(fixture.model_dump_json(indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
