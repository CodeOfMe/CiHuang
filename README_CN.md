# CiHuang（雌黄）- SVG 单元编辑器的使用说明

打开一个 SVG，点中任意一个单元，就能拖拽移动、改颜色、改形状、改文字。

> **雌黄**是古人用来涂改错字的黄色颜料：涂掉写错的，再写对的。这个工具对矢量图做的是同一件事——只挑一个零件改，别的都不动。

[English](README.md)

## 功能

- **打开任意 SVG**：按 XML 解析，不是先把图拍平成图片。
- **单击选中单个单元**：命中检测按绘制顺序倒着找，并对真实像素做判断，所以选中的是你看到的那个形状，而不是它的外接矩形。
- **批量选中一组东西**：按住 Shift 点选叠加，或者直接拖一个橡皮筋框把若干形状圈进来（比如一张图连同它上面的标题），然后一起移动。
- **成组 / 解散**：`Ctrl+G` 把选中的东西包进一个 `<g>`（之后单击就整体选中整组，和 Inkscape 一样）；`Ctrl+Shift+G` 拆开。
- **智能分组**：`Ctrl+Alt+G` 自动把挨得近的东西（比如一张图连同它的标题）聚成一类并成组。
- **不会误选背景**：点在整幅背景上，等同于点在空白处，不会把背景选中；真要选背景请从左侧元素列表点。
- **拖拽移动**：移动只会写成该单元自己的 `translate(...)`。
- **新增图形**：矩形 / 椭圆 / 直线 / 箭头 / 文字 工具，选中后在画布上拖拽即可画出（单击则落一个默认大小）。
- **连线成箭头**：用"Connect"工具从一个图形拖到另一个图形，自动生成带箭头的连线；移动图形时连线会跟着走。
- **改颜色**：用取色器设置 `fill` 和 `stroke`，也可以清成 `none`。
- **改形状**：右侧表格直接编辑该单元的原始属性（`cx`、`r`、`d`、`points`、`transform` 等），任意几何都能改。
- **改文字**：重写 `<text>` 的内容。
- **复制 / 删除 / 撤销 / 重做**，另存 SVG，导出 PNG。
- **可脚本化**：同样这些操作都有命令行、Python API 和 OpenAI 函数调用三种入口。

## 已知限制

用之前值得知道：

- 命中检测会把候选单元渲染成位图来判断，位图最长边上限 900 像素；只有外接框包含点击位置的单元才会被渲染，所以大文件也不慢。
- 编辑 `<text>` 会把它里面的 `<tspan>` 子节点替换成单一文本段。
- 成组要求所选单元属于同一个父节点；工具不会跨组重排元素。
- 连线是图形中心之间的直线（端点裁到外接框边缘）；没有正交走线或避障，也不能拖动连线控制点。
- 不动 `viewBox`、`<style>` 里的样式表或动画。
- `<image>` 和 `<use>` 可以移动、可以改颜色，但它们引用的内容不会被编辑。
- 这是标注工具，不是完整的矢量编辑器：没有贝塞尔节点编辑，也没有吸附对齐。

## 环境要求

- Python 3.10+
- `PySide6`（安装时自动装上）

## 安装

从 PyPI：

```bash
pip install cihuang
```

从源码：

```bash
git clone https://github.com/CodeOfMe/CiHuang.git
cd CiHuang
pip install -e .
```

## 快速上手

```bash
# 打开图形界面
cihuang gui examples/sample.svg

# 或者只列出文件里可编辑的单元
cihuang info examples/sample.svg
```

![CiHuang 界面](images/cihuang-gui.png)

## 用法

### 图形界面

```bash
cihuang gui drawing.svg
# 等价的写法：
cihuang-gui drawing.svg
```

鼠标左键选中并拖拽；在空白处拖动＝橡皮筋框选，按住 Shift 叠加选择。中键平移；滚轮缩放较缓，且有上下限，不会把图缩没。`Ctrl+G` 成组、`Ctrl+Shift+G` 解散、`Ctrl+Alt+G` 智能分组。右侧面板改填充、描边、文字和原始属性；左侧面板列出全部单元。

选中 **Draw** 里的工具即可新增图形：拖拽画矩形/椭圆，拖拽画直线/箭头，单击落文字；用 **Connect** 从一个图形拖到另一个图形连一条带箭头的线。按 `Esc` 回到选择工具。

### 命令行

```bash
# 列出单元（加 --json 输出机器可读结果）
cihuang info drawing.svg

# 把第 3 个单元的填充改成橙色，写出 drawing-edited.svg
cihuang fill drawing.svg 3 "#ff6600"

# 改描边，并指定输出路径
cihuang fill drawing.svg 3 "#003366" --stroke -o out.svg

# 清掉第 3 个单元的填充
cihuang fill drawing.svg 3

# 把第 1 个单元移动 dx=20、dy=-5
cihuang move drawing.svg 1 20 -5

# 重写第 5 个单元的文字
cihuang text drawing.svg 5 "新标签"

# 删除第 0 个单元
cihuang remove drawing.svg 0
```

## Python API

```python
from cihuang import SvgDocument, cihuang_set_color

# 一次性调用，写出 drawing-edited.svg
result = cihuang_set_color(input_path="drawing.svg", element_index=3, color="#ff6600")
print(result.success)   # True / False
print(result.data)      # {"output": "...", "index": 3, "prop": "fill", "color": "#ff6600"}

# 也可以直接操作文档，然后存到任意文件名
doc = SvgDocument.load("drawing.svg")
doc.push_undo()
doc.set_color(3, "fill", "#ff6600")
doc.translate(3, 20, -5)
doc.set_text(5, "新标签")
doc.save("out.svg")
```

## 智能体集成

`TOOLS` 列表把同样的操作暴露给 OpenAI 函数调用。

```python
from cihuang.tools import TOOLS, dispatch

payload = dispatch("cihuang_inspect", {"input_path": "drawing.svg"})
print(payload["success"])
```

## 开发

```bash
pip install -e ".[dev]"
ruff format . && ruff check . && pytest
```

## 许可证

GPL-3.0-or-later，见 [LICENSE](LICENSE)。
