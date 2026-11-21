# RoadSense ModelArts 训练准备

这套代码用于 Python 3.11、PyTorch、Ultralytics YOLO 的单实例单 NVIDIA GPU 训练。初始模型为 YOLO11n，默认输入 640、batch 8、100 epoch。参数是本项目的起始基线，不是对 RDD2022 原论文或获奖队配置的复现。昇腾 NPU 需要另做适配，本入口不支持 NPU。

## 本地文件

| 内容 | 路径 |
| --- | --- |
| 原始数据 | `D:\CODE\python\ict\data\raw\rdd2022\RDD2022` |
| 转换后的训练数据 | `D:\CODE\python\ict\data\processed\rdd2022-modelarts-v1` |
| 转换报告 | `data/processed/rdd2022-modelarts-v1/report.json` |
| 每张图片划分及 SHA256 | `data/processed/rdd2022-modelarts-v1/manifest.jsonl` |
| 预训练权重 | `weights/yolo11n.pt` |
| 训练入口 | `train.py` |
| 训练参数 | `configs/train.yaml` |
| 代码上传目录 | `output/modelarts/code` |
| 便携代码包 | `output/modelarts/modelarts-code.zip` |

## 数据转换与划分

```powershell
.\.venv\Scripts\python.exe -m training.prepare
```

输出目录必须不存在，版本变化请使用 `--output data/processed/rdd2022-modelarts-v2`。同磁盘默认创建图片硬链接，新增的主要空间是 YOLO 标签和清单。硬链接文件与原图共享内容，均应视为只读，不要直接修改图片；跨磁盘请明确指定 `--mode copy` 并检查空间。Windows 沙箱可能拦截创建硬链接，需在正常项目终端运行或批准对应命令。

类别编号固定为 `0 D00`、`1 D10`、`2 D20`、`3 D40`。按真实图片尺寸归一化，VOC 的 1 起始闭区间坐标转为 0 起始半开区间。越界框裁剪，退化框丢弃，其他类别统计后过滤。空标签图片保留为负样本；缺失 XML、坏 XML 或坏图片报错，不会静默当作背景。`report.json` 记录处理数量与每类分布。

每个国家按图片连续编号每 100 张建立分组，约 80% 训练、20% 验证，种子 42；相同 SHA256 的图片所属组强制归入同一集合。编号不是可靠的行程真值，跨分组近重复图片可能残留，正式评测还需独立本地路线测试集。9,035 张官方测试图片没有公开真值，不混入训练/验证；其路径保存在 `official_test.json`，本次准备的 YOLO 数据目录仅含有标注的训练来源图片。

## 上传到 OBS

使用与 ModelArts 相同区域的 OBS 桶。先运行：

```powershell
.\.venv\Scripts\python.exe scripts\build_modelarts_package.py
```

把 `output/modelarts/code/` 内的内容上传至例如 `obs://你的桶/roadsense/code/`，使 `train.py` 位于该目录根部。ZIP 是便携副本，不能假定平台会自动解压代码 ZIP。数据目录 `data/processed/rdd2022-modelarts-v1/` 单独完整上传到 `obs://你的桶/roadsense/data/rdd2022-modelarts-v1/`，保留 images、labels、清单和 READY 文件；不要上传 .venv、.tools 或本地 CPU 依赖锁文件。可使用 OBS Browser+ 或配置好凭据的 obsutil 目录上传；代码不保存 AK/SK。

## 镜像依赖

选择 Python 3.11、已配好兼容 torch/torchvision/CUDA 的 NVIDIA GPU 镜像，在镜像或 Notebook 中执行：

```bash
python -m pip install -r requirements-modelarts.txt
python -m pip check
```

依赖清单固定 Ultralytics 版本，其余包设版本范围；它不固定云端 torch/CUDA，也不是完整云端锁文件。安装前后检查 torch/torchvision，避免 pip 将镜像 GPU 版替换成 CPU 版。建议在 Notebook 验证后固化自定义镜像，训练入口不会自动 pip 安装。云端实际规格/镜像尚未验证，需在控制台确认可用版本。本地已下载预训练权重，入口默认关闭在线探测并使用随代码上传的本地权重，不依赖训练过程中下载模型。

## ModelArts 创建作业

选择自定义算法、一个实例、一张 NVIDIA GPU。预置框架模式以 `train.py` 为启动文件；自定义镜像模式按该镜像 Python 路径配置命令，例如：

```bash
python ${MA_JOB_DIR}/code/train.py
```

`code` 要与实际 OBS 代码目录最后一级名称一致。训练输入名填 `data_url`，输出名填 `train_url`，获取方式选超参或对应环境变量。新控制台映射本地输入/输出目录时，脚本直接读写映射路径，平台负责传输。若直接传 `obs://` 目录，则要求镜像包含 ModelArts moxing SDK 并具备委托权限；入口会先下载数据，训练后上传输出。moxing 不从不明 PyPI 同名包安装。

超参建议：`epochs=100`、`batch=8`、`imgsz=640`、`workers=4`、`device=0`。显存不足先降 batch。先跑 `epochs=1` 验证真实云端链路，再开始正式训练。不要选择分布式启动，本代码会拒绝 world_size 大于 1。

如果所在区域的旧控制台限制输入文件总量或数量，不要把完整数据硬塞进受限输入通道；改用已含 moxing 的镜像，将 `data_url=obs://你的桶/roadsense/data/rdd2022-modelarts-v1/` 作为普通超参交给脚本下载，或在 Notebook 中先同步至持久盘再传本地路径。预留至少 25GB 可写数据空间以及额外训练输出空间，实际按数据同步方式和缓存大小确认。

本地路径模式示例，云端根据输入输出映射路径替换：

```bash
python train.py --data_url /path/to/rdd2022-modelarts-v1 --train_url /path/to/output --device 0 --epochs 1
```

检查数据、权重、依赖、CUDA 和 GPU NMS 但不训练：在同一命令后加 `--dry-run`。纯本地检查加 `--device cpu`。

## 输出与异常

每次运行创建独立时间戳目录，记录 effective-config.yaml、dataset.runtime.yaml、run.json（版本、GPU、数据清单哈希、初始权重哈希），以及 detect/ 下的权重、results.csv 和训练日志。成功条件包括存在 `detect/weights/best.pt`；保留 last.pt 及每 10 epoch 的检查点。

脚本异常会写 error.log 并返回非零。显式 OBS 输出路径会在正常退出或捕获异常后上传当前结果；容器被强制杀死/超时/OOM Kill 时 finally 不保证执行，因此应启用平台的日志永久保存与输出通道同步，或使用 Notebook 持久盘。脚本未实现自动恢复训练，不把普通预训练权重加载宣称为断点续训。

## 验证范围

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

本地小样本测试检查的是代码链路，不代表道路病害精度。ModelArts 实际上传、委托权限、GPU 镜像和正式训练需在用户云账号中验证，本次不自动创建付费作业。

官方参考：

- https://support.huaweicloud.com/develop-modelarts/develop-modelarts-0006.html
- https://support.huaweicloud.com/intl/zh-cn/modelarts_faq/modelarts_05_0265.html
- https://docs.ultralytics.com/modes/train/
- https://github.com/sekilab/RoadDamageDetector
