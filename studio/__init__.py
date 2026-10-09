"""表情包工坊 —— 用本地 ComfyUI 把自家猫的照片做成一套动态表情包。

模块分工：

    config      读写 config.json
    comfy       只管跟 ComfyUI 说话（零依赖）
    workflows   把参数拼成 ComfyUI 的 API 工作流
    prompts     提示词模板（角色锚、风格段、8 个表情）
    cutout      色键抠图 + 去绿边
    sticker     抠好的图 → 240² 动态表情（含动效度量）
    assets      封面 / 图标 / 详情页横幅
    pack        打包成可直接上传的一整套 + 提交清单
    pipeline    把上面这些串成「一键生成」
    web         本地界面与接口

只有 sticker / assets / pack 需要 Pillow，其余全是标准库。
"""

__version__ = "1.0.0"
