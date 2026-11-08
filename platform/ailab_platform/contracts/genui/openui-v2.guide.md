## 交互与按钮

Select、Slider、Toggle、Input 的 value 写成 `$变量`，用户在页面上改动它；其它组件的参数可以用表达式读取这个变量，界面立即重新计算，不需要再问你。先用 `$变量 = 默认值` 声明默认值。例如（价格仅为示意）：

```openui
root = Stack([inputs, total, ask])
$tier = "标准版"
$seats = 20
inputs = Grid([Select("版本", ["标准版", "专业版"], $tier), Slider("人数", 5, 200, $seats, {step: 5, unit: "人"})])
total = Metric("每月费用", $seats * ($tier == "专业版" ? 99 : 49), {unit: "元"})
ask = Button("按这个方案出报价单", @ToAssistant("按" + $tier + "、" + $seats + " 人出一份报价单"))
```

切换视图用三元表达式，例如 `view = $show == "表格" ? table : chart`。Button 的第二个参数只能是 @ToAssistant("要发送的话")，可以拼接变量；点击后这句话以用户身份发出。不写 Action([...])，不用其它步骤。
