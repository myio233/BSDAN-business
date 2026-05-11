# BSDAN Business Simulator 中文说明

[English README](../README.md) | [Architecture](ARCHITECTURE.md) | [Testing](TESTING.md)

BSDAN Business Simulator 是一个面向商赛训练、课堂演示和多人复盘的网页版经营模拟平台。它把历史 ASDAN/Exschool 风格比赛表格、反推对手决策、市场份额模型、库存/产能约束、财务结算和轮次报告串成一个可以在浏览器里玩的四轮经营游戏。

它不是官方赛题引擎，也不是通用 ERP。更准确的定位是：

> 一个面向商赛训练的多人商业经营模拟平台，支持多轮经营决策、竞争结算、自动财报和房间对战。

## 功能亮点

- 支持四轮经营模拟：贷款、还款、人力、工资、产量、agent、营销、定价、管理、质量、研发和市场报告订阅。
- 单人真实原版竞争模式：玩家与 23 个历史/反推对手同场结算。
- 高强度练习模式：使用更强固定对手做策略压力测试。
- 实时多人房间：房主创建房间，真人占 canonical 队伍席位，bot 补位，逐轮准备和提交。
- 数据驱动市场预测：使用历史 workbooks 训练模型，再结合库存、产能、市场规模、主场、价格和 agent 做约束结算。
- 财务闭环：销售额、现金、债务、净资产、税费、材料、仓储、工资、专利和跨轮库存都会结转。
- 支持网页财报和图片式报告导出。
- 有覆盖核心规则的回归测试。

## 为什么仓库里有 Excel

这些 `.xlsx` 文件不是运行时隐私数据，而是项目运行所需的数据资产：

- `exschool/`：市场报告、key data 和 Team 13 轮次工作簿。
- `outputs/exschool_inferred_decisions/`：真实原版/固定对手模式需要的反推对手决策。
- `outputs/exschool_market_report_exports/`：结构化市场报告导出。
- `WYEF_results/`：模型运行和校验需要的压缩结果工作簿。

相反，`.env`、`storage/`、用户账号、session secret、SMTP 密码、Playwright 临时输出和缓存都不应该上传。

## 快速启动

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements/runtime.txt
uvicorn exschool_game.app:app --reload --app-dir . --port 8010
```

然后打开：

```text
http://127.0.0.1:8010
```

## 测试

```bash
pip install -r requirements/dev.txt
python -m pytest -q
python scripts/launch_preflight.py
```

如果安装了 Playwright：

```bash
python -m playwright install chromium
python scripts/validate_exschool_modes_playwright.py
python scripts/validate_multiplayer_room_playwright.py --human-seats 2 --bot-count 1 --rounds 1
```

## 部署提醒

生产环境至少要配置：

- 强随机 `EXSCHOOL_SESSION_SECRET`
- HTTPS 反向代理
- 可写但不进 Git 的 `storage/` 目录
- 如果要开放邮箱登录/注册，需要 SMTP 配置

示例文件在 `deploy/` 目录。
