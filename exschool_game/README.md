# Exschool Simulator

`exschool_game` 是一个基于历史 ASDAN Exschool 商赛数据构建的本地模拟器。

它的目标不是做一个通用 ERP，而是尽可能复现这个特定商赛游戏的实际规则、默认输入、对手行为和财报展示，让用户可以在浏览器里输入一轮或多轮决策，得到接近真实比赛的市场结果和图片财报。

这份文档是给第一次接手这个项目的人看的。读完之后，应该能回答这几个问题：

- 这个商赛游戏本身在玩什么
- 这个项目到底在做什么
- 代码入口在哪里
- 每一层模块各管什么
- 一轮模拟是怎么从“前端表单”一路算到“图片财报”的
- 想改规则时，应该从哪里下手，怎么避免改坏

## 1. 游戏介绍

如果你完全没见过这个项目，先不要从代码理解它，而是先把它当成一个“多轮经营模拟商赛”。

玩家扮演一家制造型公司，每一轮都要决定：

- 要不要贷款或还款
- 要增减多少工人和工程师
- 给工人和工程师发多少工资
- 这一轮计划生产多少产品
- 在每个城市放多少销售 agent
- 每个城市投多少营销费用
- 每个城市卖什么价格
- 管理、质量、研发各投多少钱
- 要不要订阅各城市的市场报告

系统会把所有队伍的决策放进同一个市场里一起结算，然后输出：

- 各城市的 CPI / market share / sales
- 公司的人力变化
- 生产、库存、仓储、研发、管理等结果
- 一份仿原始比赛样式的财报

### 1.1 这个游戏的核心资源

可以把这个游戏理解成 6 类资源之间的联动：

- 现金：所有决策最后都要落回现金能不能支撑
- 人力：工人决定零件产能，工程师决定成品产能
- 渠道：agent 决定你能否在市场里有销售触点
- 市场：不同城市有不同人口、渗透率、市场大小和竞争强度
- 库存：零件和成品都可能跨轮结转
- 技术：研发可能产出专利，专利会在后续轮次降低材料成本

### 1.2 玩家每一轮在游戏里要做什么

从玩家视角看，每一轮其实就是“填一张经营决策表”。

玩家要在一轮里完成这些决策：

#### 资金决策

- 本轮贷款还是还款
- 是否要压缩一些非核心投入，保证现金不断

#### 人力决策

- 工人数变化
- 工程师数变化
- 工人工资
- 工程师工资

这里要注意：

- 人数填的是“相对上一轮的增减”
- 工资填的是“这一轮的目标工资水平”
- 工资不只是财务成本，也会影响低薪惩罚和生产效率

#### 生产决策

- 本轮计划生产多少产品

这个数字只是“想生产多少”，不代表最后一定生产得出来。真实结果还要看：

- 工人够不够生产零件
- 工程师够不够把零件装成成品
- 上一轮有没有库存
- 当前现金是否足够覆盖材料和仓储扩容

#### 市场决策

对每个城市分别决定：

- agent 增减
- 营销投入
- 售价
- 是否订阅市场报表

这些决策会直接影响：

- 市场指数
- 理论竞争力
- 理论市场份额
- 最终销量

#### 经营投入决策

- 管理投入
- 质量投入
- 研发投入

这三类投入分别影响：

- 管理指数
- 产品质量指数
- 专利与未来材料成本

#### 玩家一轮结束后会看到什么

本轮提交后，玩家会看到：

- 公司 KPI
- 现金流/负债变化
- HR 变化
- 生产、库存、仓储结果
- 研发结果
- 各城市销量和市场份额
- 如果订阅了市场报表，还能看到该城市所有队伍的市场数据

### 1.3 后台每一轮会计算哪些系统

从后台视角看，一轮不是一个大公式，而是一串有顺序的系统结算。

目前这套模拟主要包含这些系统：

#### 系统 1：输入解释系统

负责把前端表单转成内部决策结构：

- 人数增减转为本轮目标人数
- 每个市场的 agent / 营销 / 价格转成 `MarketDecision`
- 默认值和上一轮状态拼接成当前轮次的有效输入上下文

#### 系统 2：现金截断系统

这是实际执行层的第一道关。

后台会先判断用户“想做的事”是否真的做得起：

- 工资先扣
- 生产再扣
- 再看 agent 调整能不能买得起
- 再看 marketing / quality / management / research 是否还能支付

所以用户填的是“计划”，系统执行的是“现金约束后的有效决策”。

#### 系统 3：人力系统

后台会结算：

- 本轮目标人数
- 裁员
- 低薪导致的 quitted
- 本轮新增
- 熟练员工比例
- 哪些人下轮 ready to be promoted
- 哪些人本轮正式变成 experienced

这个系统的输出既影响 HR 财报，也影响产能。

#### 系统 4：生产系统

后台会计算：

- 本轮目标产品数
- 需要多少零件
- 工人最多能生产多少零件
- 工程师最多能生产多少成品
- 在旧库存存在时，零件和成品如何衔接

也就是说，产品计划不是单独成立的，必须同时满足零件、工程师和现金约束。

#### 系统 5：库存与仓储系统

后台会维护两类库存：

- 零件库存
- 成品库存

并且跨轮延续：

- `Previous`
- `Produced`
- `Total`
- `Used/Sold`
- `Surplus`

仓储成本不是每轮重复收总额，而是按容量增量收费：

- `Capacity Before`
- `Capacity After`
- `Increment`

#### 系统 6：质量系统

后台会计算：

- `Old Products`
- `New Products`
- `Quality Investment`
- `Product Quality Index`

当前质量指数规则是：

```text
Quality Investment / (Old Products * 1.20 + New Products)
```

所以如果上一轮有成品没卖完，它会直接影响这一轮质量指数分母。

#### 系统 7：研发与专利系统

后台会处理：

- 本轮研发投入
- 累计研发池
- 本轮是否成功发明专利
- 专利数的跨轮结转
- 下一轮起材料成本乘数

专利不是立即生效，而是从下一轮开始影响材料成本。

#### 系统 8：市场竞争系统

后台会先算理论层：

- 预测 theoretical CPI
- 预测 theoretical market share
- 理论需求量

再算实际层：

- 总货量如何在各市场分配
- 是否触发主场优先
- 是否因为库存不足导致卖不到理论份额
- 是否发生 gap absorption

这个系统最后输出：

- sales volume
- market share
- sales revenue

#### 系统 9：财务系统

后台会把所有结果落到财务：

- 工资
- 材料成本
- 仓储成本
- agent 成本
- 营销投入
- 质量投入
- 管理投入
- 研发投入
- 市场报告费用
- 利息
- 税

最终得到：

- ending cash
- debt
- total assets
- net assets
- net profit

#### 系统 10：报表系统

后台最后会把所有结果重新组织成两种输出：

- 网页结果页 payload
- 图片财报 payload

这一步不应该再做新的业务判断，而应该只是把前面各系统的结果“翻译”为展示结构。

### 1.4 每一轮大致发生什么

一轮里大致按下面顺序理解：

1. 先确定本轮想投入什么资源：贷款、人力、生产、营销、管理、质量、研发。
2. 系统检查这些计划在现金上是否真的做得到。如果钱不够，就会自动截断一部分计划。
3. 根据工资、人员结构、熟练度，计算本轮真实人力和生产能力。
4. 根据计划生产、旧库存和仓储容量，算出这一轮真实可用的零件/成品数量。
5. 根据价格、管理、营销、质量、市场饱和度等，先预测理论竞争力，再分配市场份额和销量。
6. 根据销量、成本、研发、税费、利息，生成本轮财务结果。
7. 把本轮结束后的人员、库存、仓储、专利、现金、负债传到下一轮。

### 1.5 为什么会有“理论”和“实际”两层

这个游戏里很多量不是一步到位的，而是两层：

- 理论层：比如理论 CPI、理论市场份额、理论生产计划
- 实际层：比如现金不足导致计划被截断、产能不够导致无法完全生产、货量不够导致无法卖出理论份额

这个项目基本就是在尽量把这两层都复现出来。

### 1.6 这个项目当前扮演什么角色

这个仓库不是官方游戏引擎，而是一个“本地复现器”：

- 真实比赛里其它队伍会各自提交表格
- 在本项目里，Team 13 由前端用户输入
- 其它队伍用历史数据/反推数据作为固定对手输入
- 系统再按统一规则一起结算

所以接手这个项目时，最重要的不是“把代码写得更漂亮”，而是：

- 不能轻易破坏已有规则链
- 改一条规则时要知道它影响的是“理论层”还是“实际层”
- 模板、默认值、状态流转、财报展示都可能和规则本身一样重要

## 2. 项目定位

这个项目当前主要做三件事：

- 用历史 `exschool` 工作簿、市场报表、反推决策表，构建可运行的模拟环境
- 让用户在网页里填写决策，然后模拟 Team 13 的经营结果
- 输出两类结果：
  - 网页版结果页
  - 仿原始财报风格的图片财报

项目里最重要的设计原则不是“代码最短”，而是：

- 行为尽量接近真实比赛
- 每次重构不改变行为
- 所有高风险改动都要先有回归测试托底

## 3. 运行方式

在仓库根目录 `.` 下运行：

```bash
uvicorn exschool_game.app:app --reload --app-dir . --port 8010
```

然后打开：

```text
http://127.0.0.1:8010
```

也可以运行：

```bash
python -m exschool_game.app
```

## 4. 目录结构

### 核心应用

- [app.py](exschool_game/app.py)
  FastAPI 入口。负责路由、Session、页面渲染、提交表单、轮次推进、图片财报导出接口。

- [engine.py](exschool_game/engine.py)
  当前的“总编排器”。负责把各个模块串起来，执行一轮或多轮模拟。
  这里已经从早期的巨型文件拆掉不少内容，但仍然是系统主心骨。

### 已拆出的规则/数据模块

- [models.py](exschool_game/models.py)
  核心 dataclass：
  `MarketDecision`
  `SimulationInput`
  `CampaignSimulationInput`
  `CampaignState`

- [data_loader.py](exschool_game/data_loader.py)
  负责读取历史数据和默认上下文：
  `Key Data Sheet`
  市场报表
  Team 13 历史结果
  固定对手决策
  固定轮次 summary

- [campaign_support.py](exschool_game/campaign_support.py)
  负责“前端输入/默认值/跨轮状态”的辅助逻辑：
  表单输入转内部结构
  默认 payload 生成
  初始 state 构建
  下一轮 state 推进

- [workforce.py](exschool_game/workforce.py)
  人力规则：
  平均工资平滑
  低薪影响
  裁员/离职
  晋升/熟练度流转

- [research.py](exschool_game/research.py)
  研发规则：
  研发成功概率
  确定性伪随机
  专利材料成本乘数

- [market_allocation.py](exschool_game/market_allocation.py)
  市场销量分配规则：
  货量按权重分配
  主场优先
  缺口吸收
  整数分配

- [inventory.py](exschool_game/inventory.py)
  生产/库存/仓储相关纯计算：
  新零件数
  新成品数
  旧库存 + 新生产 = Total
  仓储增量
  可承受生产上限

- [finance.py](exschool_game/finance.py)
  财务辅助函数：
  贷款上限
  市场报告费用
  finance rows 现金流表组装

- [modeling.py](exschool_game/modeling.py)
  模型训练相关辅助函数：
  主场城市推断
  home-city 特征增强
  CPI -> market share 特征矩阵
  加权 R²
  share 模型训练

- [report_payload.py](exschool_game/report_payload.py)
  报表 payload 组装层：
  市场结果
  peer market tables
  HR/production/storage/research summary
  note 文案
  最终 report dict

### 渲染层

- [export_report_html.py](exschool_game/export_report_html.py)
  把 report dict 渲染成“图片财报风格”的 HTML。

- [templates/](exschool_game/templates)
  Jinja 模板。

- [static/styles.css](exschool_game/static/styles.css)
  前端样式。

### 脚本

- [scripts/screenshot_html.py](exschool_game/scripts/screenshot_html.py)
  把生成好的 HTML 转成图片。

## 5. 一轮模拟的主流程

可以把一次请求理解成下面这条链：

### A. 前端输入

用户在 [round.html](exschool_game/templates/round.html) 里填写：

- 贷款变化
- 工人数变化
- 工程师数变化
- 工资
- 管理/质量/研发投入
- 计划生产量
- 各城市 agent / 营销 / 价格 / 是否订阅市场报表

注意：

- `workers / engineers` 现在是“相对上一轮的增量/减量”
- 工资是绝对值，不是增量

### B. 路由层

[app.py](exschool_game/app.py) 负责：

- 从 Session 里恢复当前 `CampaignState`
- 组装当前轮次 `context`
- 调用 `simulator.parse_form(...)`
- 再调用 `simulator._simulate_with_context(...)`

### C. 输入标准化

[campaign_support.py](exschool_game/campaign_support.py) 和 [engine.py](exschool_game/engine.py) 会把前端 payload 变成 `SimulationInput`：

- 处理 headcount delta
- 把各市场表单输入转成 `MarketDecision`
- 保持当前轮次的默认字段和历史上下文一致

### D. 现金截断

[engine.py](exschool_game/engine.py) 中的 `_apply_cash_break_to_decision()` 会先做一轮“现金是否足够”的裁剪。

这是很重要的一层，因为用户提交的理论计划不一定真的能全部执行：

- 工资先扣
- 再判断能生产多少
- 再判断能买多少 agent
- 再判断 marketing / quality / management 能真实执行到多少

输出是一个 `effective_decision`，也就是“真正被执行的决策”。

### E. 市场模拟

`_simulate_market()` 会：

- 生成 Team 13 和固定对手的同轮市场表
- 用训练好的模型算 CPI / theoretical share
- 把总货量按市场需求分摊
- 在市场内做 gap absorption
- 得到最终销量、市场份额、收入

### F. 财务与库存

`_financial_outcome_for_team()` 会：

- 结合生产、库存、仓储、工资、agent 成本、管理、质量、研发、税
- 计算现金、资产、负债、净利润
- 决定库存如何跨轮结转
- 决定研发是否触发专利，专利从下一轮开始生效

### G. 报表组装

[report_payload.py](exschool_game/report_payload.py) 会把所有结果拼成最终 `report`：

- KPI
- Finance rows
- HR
- Management
- Production
- Storage
- Research
- Sales
- Market reports

### H. 页面/图片输出

- 网页版：Jinja 模板渲染
- 图片版：`render_report_html()` + `screenshot_html.py`

## 6. 核心规则现状

下面这些规则已经明确写进代码，不要随便“凭感觉”改：

### 6.1 市场份额模型完整流程

这是接手者最容易误判的一块，因为“页面上看到的几个指数”并不等于模型真正用到的全部输入。

当前一轮里，某个市场的份额大致按下面顺序形成：

#### 第一步：先构造该轮该市场的完整竞争表

系统会先把这一轮所有队伍放进同一个市场表里：

- Team 13 用当前前端输入或跨轮状态生成
- 其它队伍用历史/反推的固定决策
- 对每支队伍补齐：
  - `management_index`
  - `quality_index`
  - `agents`
  - `marketing_investment`
  - `price`
  - 上一轮滞后特征
  - 主场城市特征

这里用到的关键函数在：

- [engine.py](exschool_game/engine.py) 的 `_simulate_market()`
- [modeling.py](exschool_game/modeling.py)

#### 第二步：先算 theoretical CPI

系统不是直接从管理/营销/质量算市场份额，而是先训练并预测一个 theoretical CPI。

当前路径是：

1. 对市场表跑 `base_features(...)`
2. 再加 round / market context
3. 再加 home-city 相关增强特征
4. 用 CPI 模型预测 `predicted_theoretical_cpi`

也就是说，最终进入份额模型前，先有一个中间量：

```text
predicted_theoretical_cpi
```

这一步很重要，因为后面的份额模型是建立在 CPI 基础上的，而不是直接从显示出来的管理/营销/质量数值一步到位。

#### 第三步：极高价格会先被“卡货”

如果价格超过最高价 `25000` 的 `98%`，系统会先对 CPI 做一个价格惩罚：

- `98%` 最高价时，除数接近 `1`
- `100%` 最高价时，除数到 `15`

也就是说：

```text
price 太高 -> cpi 会被线性压低
```

这一步发生在份额模型之前。

#### 第四步：再把 theoretical CPI 映射成 theoretical share

这一层不是简单的 `share = cpi`，而是“base share + uplift”结构。

当前 share 模型会构造这些核心输入：

- `predicted_cpi`
- `market_slack`
- `stock_to_demand_ratio`
- `m_gate`
- `q_gate`
- `mi_gate`
- `price_gate`
- 少量交叉项

这些特征定义在 [modeling.py](exschool_game/modeling.py)：

- `market_slack`
  大致等于 `1 - 上一轮市场利用率`
  市场越空，这个值越高

- `stock_to_demand_ratio`
  大致表示“该队货量对理论需求的覆盖程度”

- 四个 gate
  不是看绝对值，而是看该队在同市场里的相对 rank 是否进入较高分位

最终 share 预测逻辑是：

- 如果模型模式是 `identity`，则 `share = cpi`
- 否则：

```text
share = cpi + uplift
```

但 uplift 只有在“供给足够”时才会真正生效。

也就是说：

- 管理/营销/质量很弱，不会把份额直接打成 0
- 它们更多影响的是 uplift 部分
- base share 仍然可能来自 `predicted_cpi`

这也是为什么你有时会看到：

- 管理指数 = 0
- 营销 = 0
- 质量 = 0

但仍然能卖出非零份额

因为当前模型下，`base share` 不是由这些显示指数单独决定的。

#### 第五步：把理论份额变成理论需求量

得到 theoretical share 后，会转成理论需求量：

```text
predicted_units_unconstrained = predicted_marketshare_unconstrained * market_size
```

这表示：

- 如果没有库存约束
- 如果没有供给吸收问题

理论上这个队伍在这个市场想卖多少。

#### 第六步：把总货量先分到各市场

每个队伍并不是每个市场都无限供货。

系统会先看每个队伍这一轮总共能卖多少产品，再按各市场理论需求比例分摊货量：

- Team 13：当前轮可卖总量 = `旧成品库存 + 本轮新成品`
- 固定对手：优先用固定决策里的 `products_planned`
- 如果缺失，才退回历史销量代理

如果一个队伍总理论需求大于总货量，系统会先按理论需求权重分配；
如果这时存在主场市场，还会对主场加权优先。

也就是说：

```text
一个队伍“总共多少货”先确定
然后这些货再在各市场之间分配
```

#### 第七步：先做第一轮实际销量

每个市场里，先做第一轮实际销量：

```text
final_sales = min(stock_in_market, cpi_demand_units)
```

于是会同时得到两件事：

- `leftover_stock`
  货没卖完

- `unmet_demand`
  理论想卖，但没货覆盖到

#### 第八步：再做 gap absorption

如果某队有剩余货量，而另一队有未满足需求，系统允许“更强的一方吃掉空缺”。

当前规则是：

- 只在同一个市场内发生
- 只要富余一方在下面任意一项上高于缺口方，就能吸收：
  - `management_index`
  - `market_index`
  - `quality_index`

而且吸收不是一次全给一个队，而是按吸收方的 theoretical CPI 比例分配。

#### 第九步：最后才得到实际市场份额

所有 gap absorption 做完后，最终市场份额才定下来：

```text
simulated_marketshare = final_sales / market_size
```

也就是说页面/财报里显示的 `market share`，不是模型直接吐出来的值，而是：

```text
theoretical share
-> 货量约束
-> 初步销量
-> gap absorption
-> 最终销量
-> 最终 market share
```

#### 第十步：为什么“指数全 0 还能卖一些”

这是接手时最容易误解的现象。

如果你在市场报表里看到某队：

- `Management Index = 0`
- `Marketing Investment = 0`
- `Product Quality Index = 0`

但它仍然有 `2%~3%` 的 market share，不一定是 bug。

原因通常是：

- 它的 `predicted_cpi` 本身不是 0
- 市场很空，`market_slack` 很高
- 它还有 agent
- 它有货
- 价格没有被极限惩罚

在当前模型里：

- 这三个显示指数并不是“份额是否非 0”的硬门槛
- 它们更多影响 uplift 或相对竞争强弱
- 不是一个“全 0 就自动卖不动”的模型

如果未来要改这个行为，应该改的是“份额模型假设”，而不是模板显示。

### 人力

- `workers / engineers` 是相对上一轮增减
- 工资输入是绝对值
- 低薪会导致：
  - 招不到计划人数
  - 员工离职
- 干满 2 轮后，员工进入熟练阶段
- 熟练员工效率 `x1.1`

### 生产/库存

- 产品和零件会跨轮结转
- Production Overview 里有：
  - `Previous`
  - `Produced`
  - `Total`
  - `Used/Sold`
  - `Surplus`
- 仓储是按“扩容增量”收费，不是每轮全额重扣

### 质量

- `Quality Index = Quality Investment / (Old Products * 1.20 + New Products)`

### 研发/专利

- 研发资金会累计
- 成功后累计清零
- 专利从下一轮开始生效
- 材料成本乘数：`0.7^n`
- 当前概率函数：

```text
1 / (1 + (x / 3)^(-1.585))
```

其中 `x` 是“百万”为单位的累计研发投入。

### 市场

- 先算 theoretical CPI / share
- 再做货量约束
- 再做 gap absorption
- 有主场优先
- 有市场饱和度和供给充足度影响

## 7. 测试入口

目前已经有一组行为回归测试，虽然还不是“覆盖一切”，但已经够支撑当前的小步重构。

测试文件：

- [test_exschool_game_hr.py](test_exschool_game_hr.py)
- [test_market_allocation.py](test_market_allocation.py)
- [test_inventory.py](test_inventory.py)
- [test_finance.py](test_finance.py)
- [test_report_payload.py](test_report_payload.py)
- [test_campaign_support.py](test_campaign_support.py)
- [test_modeling.py](test_modeling.py)

运行：

```bash
python -m pytest -q test_exschool_game_hr.py test_market_allocation.py test_inventory.py test_finance.py test_report_payload.py test_campaign_support.py test_modeling.py
```

当前基线：`24 passed`

## 8. 现在还在 engine.py 里的内容

虽然已经拆出很多模块，但 [engine.py](exschool_game/engine.py) 仍然负责：

- 总体 orchestrator
- context 生成的最后一层 glue
- 模型训练调用
- 市场模拟总装配
- 财务结果和市场结果的总整合

所以如果你看到它还比较大，不是因为没人拆，而是因为已经优先拆掉了最适合安全抽离的部分。

## 9. 维护建议

### 改规则时应该先做什么

先写测试，再改规则。

尤其是下面这些高风险链路：

- `_context_with_campaign_state`
- `_simulate_market`
- `_financial_outcome_for_team`

### 什么改动最危险

- 同时改业务规则和模板展示
- 同时改现金截断和库存逻辑
- 同时改模型训练和 market allocation

这些都很容易导致“看起来只改一点，实际整条链全漂”。

### 推荐的改动顺序

1. 先加/改测试
2. 再改纯函数模块
3. 最后才动 `engine.py` 编排层

### 不建议做的事

- 不要直接在模板里塞大量业务计算
- 不要把新规则随手加回 `engine.py` 巨函数里
- 不要先重写再验证

## 10. 如果要继续优化结构，下一步该怎么做

当前最值得继续收缩的地方有两个：

### 方向 1：继续拆 `context/state glue`

目标：

- 让 `engine.py` 更纯粹地只负责 orchestration
- 把 `_context_with_campaign_state()` 的默认值拼装再拆出去

### 方向 2：继续拆 `market simulation runtime`

目标：

- 把 `_simulate_market()` 里的组装逻辑再分层
- 让“训练模型输出”和“市场分配执行”之间边界更明确

如果只能选一个，优先选方向 1。因为它更安全，更不容易破坏游戏行为。

## 11. 最近的重构基线

最近已经做过多轮“每一步都带回归”的重构提交，重要的是这些：

- `1eb8df8` 基线快照
- `b7737b2` 抽离 `models / data_loader`
- `6e62029` 抽离 `workforce / research`
- `3269186` 抽离 `market allocation`
- `1fbc2e4` 抽离 `inventory`
- `08b5c0c` 抽离 `finance`
- `c398055` 抽离 `report payload`
- `e275931` 抽离 `campaign support`
- `abae072` 抽离 `modeling`

如果以后某次重构把行为改坏了，先用这些提交做二分定位，而不是盲猜。

## 12. 一句话总结

这个项目现在已经不是“一个 2000 多行没人敢动的巨文件”，而是一个：

- `engine` 负责总编排
- 各规则模块负责纯计算
- 测试负责托底行为

的可维护模拟器。

想维护它，最重要的是：

- 先理解数据流
- 先补测试
- 小步改动
- 每次只动一层
