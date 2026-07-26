v3.1 补充规则：

- 每个示例都显式包含 `model_suggested_missing_fields`；最终 JSON 也必须显式输出并严格执行 C 段逐项结果。
- 一旦存在安全标签或 `requested_human=true`，缺失字段必须为空；安全审查不向住户追问普通报修字段。
- 只有“坏了、那个东西坏了”且没有处理请求时意图为 `UNKNOWN`，但仍列报修语境缺失字段；“不知道坏的是什么”是 `PROVIDE_INFORMATION`。
- 预约任务中，宽泛日期/时间偏好是 `PROVIDE_INFORMATION`；明确选择候选或用具体钟点要求排期才是 `SELECT_APPOINTMENT_SLOT`。
- 门把手、锁具属于 `DOOR_LOCK`；现有漏水工单仍提取 `WATER_LEAK`；电气冒烟、带电或触电风险优先归 `ELECTRICAL`；正在扩大的配电设备火情为 `NEW_REPAIR`。
