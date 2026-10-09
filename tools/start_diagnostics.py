"""Start the local workbench with explicit mode and project-local configuration."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ('LLM_API_URL', 'LLM_API_KEY', 'LLM_MODEL', 'LLM_TIMEOUT_SECONDS',
           'ENERGY_OUTPUT_DIR', 'ENERGY_DIAGNOSTICS_DB', 'ENERGY_DIAGNOSTICS_AUDIT_DIR')


def configure(env_file: Path, mode: str):
    from dotenv import dotenv_values
    values = dotenv_values(env_file, interpolate=False) if env_file.is_file() else {}
    for key in ALLOWED:
        if values.get(key) and not os.environ.get(key):
            os.environ[key] = values[key]
    if mode == 'offline':
        for key in ('LLM_API_URL', 'LLM_API_KEY', 'LLM_MODEL'):
            os.environ.pop(key, None)
    elif mode == 'online':
        missing = [key for key in ('LLM_API_URL', 'LLM_MODEL') if not os.environ.get(key)]
        if missing:
            raise ValueError('在线模式缺少配置：' + ', '.join(missing))
    from src.diagnostics import model_status
    status = model_status()
    if status['state'] == 'invalid_configuration':
        raise ValueError('模型 URL 或超时配置无效，请检查本地 .env')
    if mode == 'online' and not status['configured']:
        raise ValueError(status['message'])
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--mode', choices=('auto', 'offline', 'online'), default='auto')
    parser.add_argument('--port', type=int, default=8010)
    args = parser.parse_args()
    try:
        status = configure(args.env_file, args.mode)
    except ValueError as exc:
        parser.error(str(exc))
    print(status['message'], flush=True)
    print(f'工作台：http://127.0.0.1:{args.port}/diagnostics', flush=True)
    import uvicorn
    uvicorn.run('src.api:app', host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
