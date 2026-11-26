"""在下面填写本趟巡检的输入，然后运行此文件。"""
from pathlib import Path
import subprocess
import sys


# 换一趟巡检时，修改视频和 GPS 路径。
VIDEO_PATH = r"C:\Users\ligua\Downloads\petal_20261122_163143.mp4"
GPS_PATH = r"output\cross_trip\trip-20261122-162846\16.28.46.gpx"
# None 表示自动推算。需要校正时可填带时区的实际开录时间。
VIDEO_START = None

# 使用同一个输出目录，让下一趟能继续匹配历史档案。
OUTPUT_ROOT = r"output\inspections"
WEIGHTS_PATH = r"results\20261121T164112Z-9daac18b\detect\weights\best.pt"
DEVICE = "cpu"

PROJECT_ROOT = Path(__file__).resolve().parent


if __name__ == "__main__":
    # 从 VS Code 或系统 Python 启动时，先切到项目环境再导入算法依赖。
    venv = PROJECT_ROOT / ".venv"
    venv_python = venv / "Scripts" / "python.exe"
    if venv_python.is_file() and Path(sys.prefix).resolve() != venv.resolve():
        raise SystemExit(subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))

    from scripts.process_trip import process_trip
    from scripts.trip_observations import BEIJING, utc_time

    output = PROJECT_ROOT / OUTPUT_ROOT
    result = process_trip(
        video_path=PROJECT_ROOT / VIDEO_PATH,
        gps_path=PROJECT_ROOT / GPS_PATH,
        start=VIDEO_START,
        weights=PROJECT_ROOT / WEIGHTS_PATH,
        output_root=output,
        device=DEVICE,
    )
    start_labels = {"manual": "手动填写", "gpx_description": "GPX 开拍说明", "video_filename": "视频文件名与 GPX 日期"}
    local_start = utc_time(result["video_start"]).astimezone(BEIJING).isoformat(sep=" ")
    print(f"录像开始时间  {local_start}（{start_labels[result['video_start_source']]}）")
    print(f"本趟确认 {len(result['observations'])} 条观测")
    print(f"档案表  {output / 'events.csv'}")
    print(f"代表图目录  {output / 'crops'}")
