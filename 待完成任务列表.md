# 待完成任务列表

## 当前先收尾

1. 收稳 round 页“真实预览”面板
   - 目标：打开“预览并提交”后，`/game/preview` 的结果能稳定渲染到预览 KPI / workforce / markets。
   - 当前状态：请求已发出，后端会回 `200`，但前端稳定性和 review 脚本联动还没完全收口。

2. 收稳浏览器常态化 review
   - 目标：`scripts/review_exschool_browser_experience.py` / `.sh` 能稳定产出 `summary.json` 和 `review.md`。
   - 检查项：
     - auth / high-intensity / real-original / multi-save 功能流
     - round preview 是否真正出数
     - report-vs-real alignment
     - 文案冗余 / 英文残留

3. 继续压 `real-original` 的 `r1 all-company` 偏差
   - 主线：以 `all_company_standings` 和真实财报为准，不再只看 Team13 单点。
   - 当前关注公司：`C17`、`C3`、`C9`、`C10`、`C22`
   - 约束：不改三段链 `CPI -> predicted_marketshare_unconstrained -> 缺口吸收`

## 已完成

4. 实时多人游戏 MVP
   - 房主可创建房间
   - 房主可设置真人席位与 bot 数量
   - 人满后才允许开始
   - 所有人点击“已准备”后才正式开始
   - 开始后统一 40 分钟倒计时
   - 房间支持超时自动提交默认输入
   - 游戏中可实时查看其它玩家的准备/提交状态
   - 真人席位会覆盖 canonical 队伍，其他队伍继续按 fixed opponents 参赛

5. 多人房间 bot 机制
   - bot 按最强到最弱顺序补位
   - 当前包含 `C13`
   - bot 仍走真实财报反推输入

6. 多人模式验证
   - `test_multiplayer_mode.py`
   - `test_multiplayer_store.py`
   - `scripts/validate_multiplayer_room_playwright.py`

## 继续优化 / 非阻塞尾项

7. 多人模式继续增强
   - room 页面的人类提交状态还可以再压得更显眼
   - 如果要严格“纯浏览器按钮流”验收，room 页 start/join/ready 的异步体验还可以再收稳
   - 继续考虑把多人验证接入 launch preflight / 常态化 review 汇总
