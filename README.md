# Polaris（北极星）

一个求职个人 agent：按方向采集 LLM 和 Agent 方向的实习岗位，抽取、去重、匹配，每天给出简报，并跟进每一次投递。为 Nebius × NVIDIA Global AI Hackathon 开发。

> 开发中。英文版 README、架构图、指标总表在 M10 补齐。当前进度见 [docs/STATUS.md](docs/STATUS.md)。

## 本地运行

需要 Docker 和 [uv](https://docs.astral.sh/uv/)。

```bash
cp .env.example .env          # 填入模型服务的地址、key、模型名
docker compose up -d --build  # 起数据库和服务，自动建表
```

打开 http://127.0.0.1:8000/docs 可以看到全部接口并直接试调。

```bash
uv sync                        # 本机开发环境
uv run polaris seed            # 合成数据入库
uv run polaris metrics m1      # 生成 eval/reports/M1.md
uv run pytest                  # 测试
```

## 文档

- [docs/PURPOSE.md](docs/PURPOSE.md)：为什么做
- [docs/PLAN.md](docs/PLAN.md)：总体框架和模块顺序
- [docs/design/](docs/design/)：各模块的设计
- [eval/reports/](eval/reports/)：各模块的指标

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
