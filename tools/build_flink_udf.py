"""Build the Flink raw-event UDF JAR using the existing local Flink image.

All compiler inputs and outputs stay under D:\\industrial_energy_analysis\\streaming\\build.
The image must already exist; this script never downloads an image implicitly.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "streaming" / "build"
IMAGE = "flink:1.20.2-scala_2.12-java17"
API_JAR = "flink-table-api-java-uber-1.20.2.jar"
OUTPUT_JAR = BUILD / "industrial-energy-raw-udf.jar"


def main() -> None:
    if subprocess.run(["docker", "image", "inspect", IMAGE],
                      capture_output=True, check=False).returncode != 0:
        raise SystemExit(f"Existing Docker image required; refusing implicit download: {IMAGE}")
    for command in ("javac", "jar"):
        if shutil.which(command) is None:
            raise SystemExit(f"JDK 17 command unavailable: {command}")
    BUILD.mkdir(parents=True, exist_ok=True)
    class_dir = BUILD / "classes"
    class_dir.mkdir(exist_ok=True)
    api_path = BUILD / API_JAR
    if not api_path.is_file():
        subprocess.run([
            "docker", "run", "--rm", "--entrypoint", "/bin/bash",
            "--volume", f"{BUILD.resolve()}:/build", IMAGE, "-lc",
            f"cp /opt/flink/lib/{API_JAR} /build/{API_JAR}",
        ], check=True)
    sources = sorted((ROOT / "streaming" / "java").glob("*.java"))
    if not sources:
        raise SystemExit("No Flink UDF Java sources found")
    subprocess.run(["javac", "--release", "17", "-cp", str(api_path),
                    "-d", str(class_dir), *(str(path) for path in sources)], check=True)
    subprocess.run(["jar", "--create", "--file", str(OUTPUT_JAR),
                    "-C", str(class_dir), "."], check=True)
    print(f"FLINK_UDF_BUILT {OUTPUT_JAR}")


if __name__ == "__main__":
    main()
