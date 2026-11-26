# 病害档案导入与详情查询

巡检程序将累计档案写入 `output/inspections/events.csv`。每完成一趟巡检，对同一个数据库再运行一次导入，前端即可通过病害编号查到最新档案、代表图和观测时间线。

## 在这台 Windows 电脑上查看巡检数据

将 `events.csv` 和同级的 `crops/` 放到 `output/inspections/`。在仓库中双击 [backend/start-local.cmd](./start-local.cmd)，脚本会把档案导入本机 `road_inspection` 数据库，再启动预览。服务只允许这台电脑访问。如果服务已经在运行，使用下方地址查看。更新巡检数据后，可关闭正在运行的服务，再双击脚本更新本机库。

| 打开地址 | 可以验证什么 |
| --- | --- |
| `http://127.0.0.1:8017/map/` | 地图和病害列表。点击点位后选择“查看病害详情”，或直接点击列表中的编号 |
| `http://127.0.0.1:8017/` | 病害详情预览网页，输入编号可查看观测时间线和代表图 |
| `http://127.0.0.1:8017/api/defects/1` | 查询编号为 1 的档案，也可将 1 替换为其他实际编号 |
| `http://127.0.0.1:8017/api/defects/1/image` | 查询编号为 1 的代表图 |
| `http://127.0.0.1:8017/docs` | 接口参数、响应结构和在线试调 |

本机 PostgreSQL 和 Python 依赖位于仓库的 `.tools/`，巡检输入位于 `output/`，Git 会忽略这两个目录。其他电脑可以按下文安装依赖，连接自己的 PostgreSQL 并导入巡检产物。

地图通过 `GET /api/defects` 读取同一个 PostgreSQL 数据库。底图使用 `web/config.js` 中的高德配置。缺定位的病害和底图加载失败时，都可以从病害列表访问详情。

## 接到华为云 RDS PostgreSQL

1. 在华为云准备 **RDS for PostgreSQL** 实例，为项目创建独立数据库，例如 `road_inspection`，让导入账号成为该库的所有者。表和索引由首次导入自动创建。前端调用 HTTP 接口，只有后端和导入程序需要数据库账号。
2. 选择能访问实例内网的运行位置，例如与 RDS 同区域、同 VPC 的 ECS，并在安全组中允许后端来源访问实例的数据库端口。使用 RDS 内网地址时，本地电脑需要有通往该 VPC 的网络连接。也可以把项目、CSV 和同级的 `crops/` 传到 ECS 后执行导入。
3. 在 RDS 实例的“连接管理”里记下读写地址、端口，下载并解压 SSL 证书。下面的 Windows 示例使用华为云内网 IP 连接示例中的 `verify-ca`。使用与证书匹配的主机名时，可以设为 `verify-full`。 [华为云连接指南](https://support.huaweicloud.com/qs-rds-pg/rds_02_0016.html) · [PostgreSQL 证书校验说明](https://www.postgresql.org/docs/current/libpq-ssl.html)

以下 PowerShell 命令在项目根目录执行。先准备独立的后端环境，Python 3.11 或更新版本均可用于这套代码。

```powershell
Set-Location E:\ICT\ict
python -m venv .tools\backend-venv
.\.tools\backend-venv\Scripts\python.exe -m pip install -r requirements-backend.txt
```

再把这些值替换为实例的实际连接信息。在当前终端输入密码，密码提示不会回显。启动导入和 API 的进程都需要相应的连接设置。也可以使用已有的 PostgreSQL 服务配置或密码文件。

```powershell
$env:PGHOST = '实例读写地址'
$env:PGPORT = '5432'
$env:PGDATABASE = 'road_inspection'
$env:PGUSER = '项目数据库用户名'
$env:PGSSLMODE = 'verify-ca'
$env:PGSSLROOTCERT = 'C:\certs\ca.pem'
$env:PGPASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host '数据库密码' -AsSecureString)).Password
```

准备真实数据时，在采集环境中运行项目的 `run_inspection.py`，或向算法同学索取整个 `output/inspections/` 目录。选取表头含 `defect_id`、`observation_count` 的最终档案。同一输出目录沿用同一套编号。不同采集目录应使用独立数据库，以免相同编号指向不同病害。

```powershell
.\.tools\backend-venv\Scripts\python.exe -m backend.import_events .\output\inspections\events.csv
.\.tools\backend-venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

在运行 API 的电脑上访问 `http://127.0.0.1:8000/api/defects/1`，将末尾的 `1` 替换为 CSV 里的实际编号。若 API 已部署在 ECS，前端通过你们应用的 HTTPS 入口转发 `/api/` 到该服务即可。数据库密码只保存在后端运行环境。

在 Linux ECS 上，对应的安装和运行命令如下。在终端用 `export` 设置同名的 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGSSLMODE`、`PGSSLROOTCERT`。证书路径填 ECS 上的实际路径，密码可通过本机的 PostgreSQL 密码文件提供。

```bash
python3 -m venv .tools/backend-venv
.tools/backend-venv/bin/python -m pip install -r requirements-backend.txt
.tools/backend-venv/bin/python -m backend.import_events output/inspections/events.csv
.tools/backend-venv/bin/python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

## CSV 中的一处病害怎样变成 API 数据

| 来源 | 数据库存储 | API 返回 |
| --- | --- | --- |
| CSV 每行的 `defect_id`、`class`、位置和汇总字段 | `inspection.defects`，以病害编号为主键 | `defect_id`、`type`、`lat`、`lon`、`first_seen`、`last_seen`、`observation_count` 等 |
| `visits` 单元格中每个巡检编号对应的记录 | `inspection.observations`，以病害编号和巡检编号共同标识 | `observations` 数组，按观测时间从早到晚排列，时间未知的排在最后 |
| `crop` 指向的 `crops/*.jpg` | `inspection.images`，每处病害保存一张代表图的 JPEG 内容 | `image_url` 指向同一个 API 服务，可直接作为图片地址 |

- 数据库和详情接口保留巡检的 WGS84 经纬度。地图接口为高德底图转换出 GCJ-02 坐标。没有可靠 GPS 时，两套坐标均为 `null`，病害仍可在列表中查询。每次观测还保留 `gps_quality`。
- 观测时间带时区，接口统一输出 UTC。网页按浏览器所在时区显示观测时间。
- 代表图属于整处病害。只有 CSV 时，导入器会报告本地缺少的代表图数量。把图片补到 CSV 同级的 `crops/` 后重新导入，原来的病害编号即可查到图片。数据库中已有同路径代表图时会继续提供该图。
- 单份 CSV 在一个事务中导入。重复导入保留累计次数，新巡检增加一条观测。导入器会拒绝累计次数倒退或丢失已知观测的病害记录。旧版 CSV 只有累计数时，接口保留这个数，并只返回实际存在的观测明细。
- 已知病害返回 `200`，未知编号或缺图时对应接口返回 `404`，非法编号返回 `422`。数据库未连接或尚未完成初始化时，详情接口返回 `503`。

## 复查导入逻辑

在专用测试 PostgreSQL 实例中设置连接信息，使用可创建临时数据库的账号。测试会新建随机名称的数据库，执行后清理。

```powershell
$env:INSPECTION_TEST_POSTGRES = '1'
.\.tools\backend-venv\Scripts\python.exe -m unittest discover -s tests -p test_inspection_backend.py -v
```
