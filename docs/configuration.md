# 配置指南

[返回项目首页](../README.md)

## 配置文件与首次使用

首页快速开始使用 `config.example.json` 作为起点：OCR 日常已启用，模拟器参数和通知留给使用者填写。签到与连登奖励共用 `game_flow: claim_login_rewards`，无需模板；旧配置可将 `signin` 的步骤替换为该流程。邮件及活动模板不附带个人截图，需要裁剪后启用。

`python -m hka --config config.json init` 则生成更保守的通用配置，只启用启动游戏。已有 `config.json` 时不要再次初始化。

配置完成后，先「检测连接」，查看截图，再「仅测试选中任务」。按钮、游戏布局与账号进度符合预期后，运行今日任务。

## 每日任务与执行结果

「任务流程」中列表顺序就是执行顺序。启用任务并通过「上移／下移」调整，随后点击顶部「保存配置」。旧页面不能覆盖已更新的配置；提示版本已更新时先刷新。

- 「运行今日任务」：执行所有启用、日期匹配且当日未完成的任务。
- 「仅测试选中任务」：只测试选中的一项，可强制重跑。
- `runtime.daily_reset_at`：每日分界时间。示例为 `04:00`，此前运行计入前一游戏日。
- `runtime.stop_on_error`：普通任务出错时是否停止后续任务。
- `required: true`：必要任务失败时停止本轮。
- `always_run: true`：还有待办任务时，重新执行该准备任务。

没有执行的任务及原因记录在报告的 `not_run` 中；请同时查看日志区分日期不匹配、已完成、未启用和前序失败。

## BONUS 与战斗选项

主流程的 `game_flow: stamina_farm` 常用选项：

| 字段 | 作用 |
| :--- | :--- |
| `first_clear: "auto"` | 检查首通，补推未完成关卡，并继续识别新解锁关卡 |
| `first_clear: false` | 跳过首通检查，直接寻找 BONUS |
| `min_stamina: 100` | 普通体力准入门槛，资源保护不允许降至 100 以下 |
| `max_runs` | 本阶段最多完成的 BONUS 局数 |
| `use_double_ticket` | 使用双倍券；流程核对详情页的选中状态与体力消耗 |
| `combat.mode: "move_shoot"` | 移动与射击 |
| `combat.mode: "buttons"` | 仅按配置操作按键，适用于自动套 |

`combat.skills` 提供三个技能按键，`mode` 可选 `off`、`tap`、`hold`、`once`。`once` 只在进图时使用一次；`hold_ms` 设置长按时长。按键位置按 1600×900 的基准画面填写，输入顺序执行。

双倍体力额外任务 `bonus_double_stamina` 使用独立的双倍体力识别与兑换核对，每日最多一局；固定关闭双倍券。领取存在感后的 BONUS 使用单独任务，示例上限为一局，之后再次领取存在感。

## 自定义步骤

支持启动游戏、等待图像、点击图像、比例坐标点击／滑动、返回键、等待、截图、重复和图像条件分支。内置 OCR 流程通过 `game_flow` 步骤调用。

活动任务示例，需替换日期并制作对应模板：

```json
{
  "id": "my_event",
  "name": "活动奖励",
  "kind": "event",
  "enabled": false,
  "schedule": {"type": "range", "start": "2099-01-01", "end": "2099-01-07"},
  "steps": [
    {"type": "wait_image", "image": "home.png", "timeout": 30},
    {"type": "tap_image", "image": "event_entry.png", "on_missing": "skip_task"},
    {"type": "repeat", "times": 10, "steps": [
      {"type": "tap_image", "image": "event_claim.png", "timeout": 2, "on_missing": "break_repeat"}
    ]},
    {"type": "key", "code": 4}
  ]
}
```

图像步骤选项：

| 字段 | 含义 |
| :--- | :--- |
| `threshold` | 匹配阈值，默认 `0.88` |
| `region` | `[左, 上, 右, 下]`，均为 0～1 的屏幕比例 |
| `timeout` | 匹配超时秒数 |
| `wait` | 步骤后的等待秒数 |
| `offset_x` / `offset_y` | 相对匹配位置的点击偏移 |
| `on_missing` | `fail`、`continue`、`skip_task`，循环内也可用 `break_repeat` |

`if_image` 用 `then` 和 `else` 编写两组步骤。固定坐标适用于稳定布局；涉及购买、刷新与兑换的自定义流程，应补充价格、次数与确认框核对。

## 日期规则

| 场景 | `schedule` 示例 |
| :--- | :--- |
| 每天 | `{"type":"daily"}` |
| 周一、三、五 | `{"type":"weekdays","days":[0,2,4]}`，周一为 0 |
| 每年指定日期 | `{"type":"dates","dates":["04-13","08-13"]}` |
| 指定某天 | `{"type":"dates","dates":["2099-01-01"]}` |
| 活动日期范围 | `{"type":"range","start":"2099-01-01","end":"2099-01-07"}` |

角色生日及节日奖励的日期和入口，以对应区服公告及实际游戏画面为准；示例不代表已完成该活动的适配。

## 通知

在「通知」页配置 HTTP Webhook 或 SMTP，并选择成功／失败是否发送。

Webhook 发送 `POST` JSON，请使用接受此结构的接收端或转换服务：

```json
{"title":"任务结果","content":"执行摘要","success":true}
```

邮件使用 STARTTLS；密码从 `notifications.email.password_env` 指定的环境变量读取，默认 `HKA_SMTP_PASSWORD`。请在启动助手前设置环境变量，配置文件中不填写密码。

## 命令行

在项目目录运行，以下操作使用已有配置：

```powershell
# 检查连接与游戏安装
.\.venv\Scripts\python.exe -m hka --config config.json doctor

# 查看今天待办
.\.venv\Scripts\python.exe -m hka --config config.json plan

# 手动执行整套流程
.\.venv\Scripts\python.exe -m hka --config config.json run

# 强制测试单个任务
.\.venv\Scripts\python.exe -m hka --config config.json run --task axis_garden --force

# 获取截图
.\.venv\Scripts\python.exe -m hka --config config.json capture --output capture.png

# 手动启动本地网页界面
.\.venv\Scripts\python.exe -m hka --config config.json serve
```

### 可选定时运行

仅当你主动需要定时运行时，在命令行启动：

```powershell
.\.venv\Scripts\python.exe -m hka --config config.json daemon --at 07:30
```

使用电脑本地时间，需要电脑开机、该进程持续运行且模拟器可连接。它仍遵守任务日期和当天完成记录。网页与 `打开助手.vbs` 默认手动待机，不自动启动此命令。

## 本地文件与排查

| 位置 | 内容 |
| :--- | :--- |
| `config.json` | 模拟器、任务和通知设置 |
| `templates/*.png` | 从实际游戏画面裁剪的模板 |
| `state.json` | 每日完成记录 |
| `runs/*.json` | 执行结果、局数、停止原因 |
| `runs/*.png` | 失败时的游戏画面 |
| `runs/assistant-service.log` | 助手后台日志 |

任务识别失败时，先查看日志与错误截图，检查当前页面、分辨率和 OCR 内容。提交 Issue 可附上任务名称、报错与已遮盖账号信息的截图；个人配置、游戏截图和通知地址默认留在本地。
