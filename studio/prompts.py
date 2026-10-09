"""提示词模板 —— 这个项目的灵魂部分。

**一、角色锚必须逐字复制到每一张。**
模型没有记忆，它认角色的唯一依据是参考图 + 提示词里那段不变的角色描述。
所以角色描述只能定义一次，被每一张引用，不允许各张自己改字。

**二、定妆照是唯一参考图，也是整批一致性的唯一来源。**
第一张（定妆照）错了，后面十六张全跟着错。所以界面上把「先看一眼定妆照」
做成一个可选项 —— 花 20 秒看一眼，比后面重出十六张便宜得多。

**三、画面内的字是让模型画的，不是脚本加的。**
实测：单行 2~4 个汉字，模型 8/8 全对，字形笔画都对。
所以这里不去做「后期用字体打字」那套 —— 字体的位置、描边、和画面的贴合度
都不如模型自己画。⚠️ 边界是 4 个字：再长就开始缺笔画、错字。

（这条跟年历那件正好相反：年历的日期网格 0/4 全错，必须脚本加字。
结论不是「模型能画几个字」，而是「哪类字能让模型画」。）

**四、主角不一定是猫。**
`subject` 就是读者填的那句「照片里的主角是什么」。按它落进三类模板：
**动物 / 人物 / 物件**，各有各的「保特征」句和负面词 ——
因为「毛色花纹」对人不成立，「发型五官」对玩偶也不成立。

⚠️ 改动纪律：`subject="猫"` 时，这个文件产出的提示词必须与只支持猫的那版
**逐字节相同**。`tests/test_prompts.py` 把那一版冻住了，改完跑一遍再提交，
否则就是把实测过的效果悄悄改掉了。
"""
import re

# ---------------------------------------------------------------- 主角

DEFAULT_SUBJECT = "猫"
SUBJECT_MAX = 16            # 读者填的「主角」最多留这么长，再长就不是名词了

KIND_ANIMAL, KIND_PERSON, KIND_OBJECT = "动物", "人物", "物件"

# 界面上给的那几个快选（也可以自己填）
SUBJECT_CHIPS = ["猫", "狗", "兔子", "仓鼠", "鹦鹉", "小朋友", "毛绒玩偶"]

# ⚠️ 物件先判、人物后判。因为「我女儿的毛绒玩偶」里两边的词都有，
# 它真正的主角是玩偶 —— 顺序反过来就会判成人物。
# 也正因如此这里**不能收「布偶」**：「布偶猫」是个猫的品种名，
# 收了它就会把一只猫判成物件。
OBJECT_HINTS = ("玩偶", "公仔", "娃娃", "手办", "毛绒", "抱枕", "靠垫",
                "书包", "背包", "杯子", "马克杯", "钥匙扣", "挂件", "摆件",
                "面具", "头盔", "雪人")
PERSON_HINTS = ("人", "女孩", "男生", "女生", "宝宝", "小朋友", "孩子",
                "女儿", "儿子", "爸爸", "妈妈", "爷爷", "奶奶", "外公", "外婆",
                "老公", "老婆", "男朋友", "女朋友", "姐姐", "哥哥", "弟弟", "妹妹",
                "阿姨", "叔叔", "大叔", "闺蜜", "同事", "同学",
                "自己", "本人", "他", "她")

DESC_HINT = {
    KIND_ANIMAL: "例：橘白相间，额头有白色竖纹，胸口一大片白，尾巴橘色带条纹，眼睛是琥珀色",
    KIND_PERSON: "例：短发，圆脸，戴一副黑框眼镜，常穿深蓝色外套，笑起来有酒窝",
    KIND_OBJECT: "例：灰白色毛绒，圆眼睛，鼻子是深棕色，脖子上系着一根红绳",
}

WHOSE = {KIND_ANIMAL: "它", KIND_PERSON: "他", KIND_OBJECT: "它"}


def clean_subject(subject):
    """把读者填的那句话收拾成能直接塞进提示词的一个名词短语。

    换行会让提示词断句，多个空格会让模型把词拆错，所以都压掉。
    """
    s = (subject or "").strip()
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s[:SUBJECT_MAX] or DEFAULT_SUBJECT


def kind_of(subject):
    """主角属于哪一类。猜不出来就当动物 —— 这是这个工具最初的场景。"""
    s = clean_subject(subject)
    for w in OBJECT_HINTS:
        if w in s:
            return KIND_OBJECT
    for w in PERSON_HINTS:
        if w in s:
            return KIND_PERSON
    return KIND_ANIMAL


def desc_hint(subject):
    """第 2 步输入框里那句举例。写「毛色花纹」对人是没用的，所以按类切换。"""
    return DESC_HINT[kind_of(subject)]


def whose(subject):
    """提示词里指代主角的人称。人用「他」，其余用「它」。"""
    return WHOSE[kind_of(subject)]


def _noun(subject):
    """有参考图时，指代主角的说法。猫 → 「这只猫」。"""
    k = kind_of(subject)
    if k == KIND_PERSON:
        return "这个人"
    if k == KIND_OBJECT:
        return "这个%s" % subject
    return "这只%s" % subject


def _one(subject):
    """不带参考图、凭空画时的说法。猫 → 「一只小猫」。"""
    k = kind_of(subject)
    if k == KIND_PERSON:
        return "一个人"
    if k == KIND_OBJECT:
        return "一个%s" % subject
    return "一只小%s" % subject


def _desc_sentence(subject, desc):
    """把「主角是什么」和读者写的那句特征，合成提示词里的一句。

    ⚠️ 动物那一支必须保持原样：「它是一只{特征}的小{名词}。」
    """
    d = (desc or "").strip()
    if not d:
        return ""
    k = kind_of(subject)
    if k == KIND_PERSON:
        return "照片里是%s，%s。" % (subject, d)
    if k == KIND_OBJECT:
        return "这是一个%s，%s。" % (subject, d)
    return "它是一只%s的小%s。" % (d, subject)


# ---------------------------------------------------------------- 固定段

# 角色锚：从这个项目的八张表情到横幅，逐字相同的一小段。
# 读者不用改 —— 改的是界面上那句「它是什么」和「它长什么样」。
# 人称留成占位符：人像要写「他」，动物和玩偶写「它」。
FIXED_POSE_T = ("{ta}正面站立，双手自然垂在身体两侧，眼睛睁大看向前方，"
                "表情是平静的微笑，全身从头到脚都在画面内。")

# 只支持猫的那一版就长这样，留这个名字给外部引用，值不变。
FIXED_POSE = FIXED_POSE_T.format(ta="它")


def fixed_pose(subject):
    """角色锚，人称跟着主角走。"""
    return FIXED_POSE_T.format(ta=whose(subject))

# 风格段：逐字相同。「纯绿色背景」是刻意的选择 ——
# 表情包要透明底，但让模型直接给「透明」不可靠，给一块纯色反而抠得干净。
STYLE = ("Q版卡通表情包风格，粗黑描边，扁平明亮配色，高对比，主体居中占满画面，"
         "全身可见，纯绿色背景（嫩绿 #26ED16），画面干净，没有多余装饰。")

NO_TEXT = "画面里没有任何文字。"

# 保特征：这一句是「还是我家那个」的关键，实测比不写明显更像。
# 三类各写一句 —— 猫用「毛色花纹」，人用「发型五官」，玩偶用「材质图案」。
KEEP_ANIMAL = ("请严格保留这只{s}的毛色分布、花纹位置和眼睛颜色，画出来的必须还是同一只{s}，"
               "不要换一个品种，不要改变花纹。")
KEEP_PERSON = ("请严格保留这个人的发型、脸型、五官、肤色和照片里的穿着特点，"
               "画出来的必须还是同一个人，不要换成另一张脸。")
KEEP_OBJECT = ("请严格保留这个{s}的颜色、材质、图案和配件，画出来的必须还是同一个{s}，"
               "不要换成别的东西。")

NEGATIVE_BODY = ("水印，签名，二维码，网址，多余的文字，错别字，乱码，模糊，低分辨率，"
                 "白边，位置偏移，大小改变，画面构图改变，深色背景，白色背景，透明背景")
NEGATIVE_ANIMAL = ("多个角色，两只{s}，不同的动物，风格不一致，写实照片，" + NEGATIVE_BODY)
NEGATIVE_PERSON = ("多个角色，两个人，不同的长相，风格不一致，写实照片，真人照片，" + NEGATIVE_BODY)
NEGATIVE_OBJECT = ("多个角色，两个{s}，不同的东西，风格不一致，写实照片，" + NEGATIVE_BODY)

# 只支持猫的那一版就长这样，留这两个名字给外部引用，值不变。
KEEP = KEEP_ANIMAL.format(s=DEFAULT_SUBJECT)
NEGATIVE = NEGATIVE_ANIMAL.format(s=DEFAULT_SUBJECT)
BANNER_NEGATIVE = NEGATIVE + "，文字，白色背景，白底，纯白背景，透明背景，绿幕，深色背景"

# 横幅另有三条硬要求：避免任何文字、避免白底、避免透明底。
BANNER_TAIL = "文字，白色背景，白底，纯白背景，透明背景，绿幕，深色背景"


def keep_for(subject):
    """「还得是同一个」那一句，按主角类别选。"""
    s = clean_subject(subject)
    k = kind_of(s)
    if k == KIND_PERSON:
        return KEEP_PERSON
    if k == KIND_OBJECT:
        return KEEP_OBJECT.format(s=s)
    return KEEP_ANIMAL.format(s=s)


def negative_for(subject):
    """负面词。按主角类别选，避免出现「两只人」这种自相矛盾的说法。"""
    s = clean_subject(subject)
    k = kind_of(s)
    if k == KIND_PERSON:
        return NEGATIVE_PERSON
    if k == KIND_OBJECT:
        return NEGATIVE_OBJECT.format(s=s)
    return NEGATIVE_ANIMAL.format(s=s)


def banner_negative(subject):
    return negative_for(subject) + "，" + BANNER_TAIL


# ---------------------------------------------------------------- 三种提示词

def hero_prompt(desc="", from_photo=True, subject=DEFAULT_SUBJECT):
    """定妆照。整套表情的一致性从这里开始。

    `desc` 是读者填的那句「它长什么样」（毛色花纹 / 发型五官 / 材质图案）。
    照片负责长相，文字负责把它说清楚 —— 两个一起给，比只给一个稳。
    """
    subject = clean_subject(subject)
    if from_photo:
        head = "Q版卡通表情包角色设定图：把参考照片里的%s画成卡通角色。" % _noun(subject)
    else:
        head = "Q版卡通表情包角色设定图："
    parts = [head]
    d = _desc_sentence(subject, desc)
    if d:
        parts.append(d)
    parts.append(fixed_pose(subject))
    if from_photo:
        parts.append(keep_for(subject))
    parts.append(STYLE)
    parts.append(NO_TEXT)
    return "\n\n".join(parts)


def frame_prompt(text, state, desc="", from_photo=True, subject=DEFAULT_SUBJECT):
    """一张表情。`state` 是这一帧的动作与神态。

    同一条表情的两帧之间：**只有 `state` 那一句不同，其余逐字相同**，
    并额外加一句锁构图的话。这是让 GIF 里「只有该动的地方动了」的唯一办法。
    """
    subject = clean_subject(subject)
    state = state_for(state, subject)
    head = ("Q版卡通表情包：%s\n\n"
            % ("把参考照片里的%s画成卡通角色" % _noun(subject) if from_photo
               else _one(subject)))
    d = _desc_sentence(subject, desc)
    if d:
        head += d + "\n\n"
    lock = ("严格保持与参考图完全相同的姿势、位置、大小、朝向和画面构图，"
            "只改变下面说明的这一处，其余一切照旧。")
    return ("%s%s\n\n动作与神态：%s。\n\n%s\n\n%s\n"
            "画面下方有一行居中的中文大字「%s」，白色描边黑色字体，"
            "字形完整正确、笔画清晰可读，文字不能遮挡角色的脸，"
            "文字四周要留出空白，不要贴到画面边缘。"
            % (head, lock, state, STYLE, NO_TEXT, text))


def banner_prompt(desc="", subject=DEFAULT_SUBJECT):
    """详情页顶部那条横幅 750×400。

    ⚠️ 官方对这条横幅另有三条要求：**避免任何文字、避免白底、避免透明底**。
    所以这里用淡蓝底，负面词里也把「白色背景 / 透明背景 / 文字」全列进去。
    —— 这三条最容易漏，第一版给米白底就是不合格的。
    """
    subject = clean_subject(subject)
    head = "Q版卡通横幅插画：把参考照片里的%s画成卡通角色。" % _noun(subject)
    d = _desc_sentence(subject, desc)
    style = ("Q版卡通插画风格，粗黑描边，扁平明亮配色，高对比，淡蓝色纯色背景，"
             "画面干净，留白充足，没有多余装饰。")
    scene = ("%s坐在画面中央偏右的位置，侧身回头看过来，眼睛睁大，表情开心，"
             "左侧下方散落着几片小叶子。横版构图，主体与装饰之间留出干净的间隔。"
             % whose(subject))
    return "\n\n".join(x for x in [head, d, scene, keep_for(subject), style,
                                   "画面里没有任何文字。"] if x)


# ---------------------------------------------------------------- 表情词表

# 八个表情 × 2 帧。
# 每一对只差「动作与神态」那一句 —— 别的一个字都不能动，否则 GIF 会全身乱跳。
# 文案是 2~4 个汉字，这是模型能画对的上限区间。
# ⚠️ 这里写的是**动物**的说法（前爪、耳朵）。人像要用同一套动作，
#    由 state_for() 在拼提示词时改字 —— 词表本身不动，猫的效果就不变。
DEFAULT_MOODS = [
    {"text": "收到",
     "frames": ["它双手合十举在胸前，头微微低下鞠躬，耳朵向后压，表情认真",
                "它双手合十举在胸前，头抬起来直视前方，耳朵向后压，表情认真"]},
    {"text": "好的",
     "frames": ["它竖起一个大拇指，手放在身前较低的位置，眯着眼睛笑",
                "它竖起一个大拇指，手举到脸旁边较高的位置，眯着眼睛笑"]},
    {"text": "摸鱼中",
     "frames": ["它趴在桌面上，下巴搁在手臂上，眼睛半睁半闭，一副懒散的样子",
                "它趴在桌面上，下巴搁在手臂上，眼睛完全闭上，头顶飘着三个小小的睡眠气泡"]},
    {"text": "晚安",
     "frames": ["它闭着眼睛张大嘴打哈欠，一只前爪抬起揉眼睛，身体歪向一侧",
                "它闭着眼睛把嘴合上，一只前爪贴在脸边，身体歪向一侧"]},
    {"text": "冲鸭",
     "frames": ["它握紧拳头向前奔跑，左腿在前，身体前倾，嘴巴张开大喊",
                "它握紧拳头向前奔跑，右腿在前，身体前倾，嘴巴张开大喊"]},
    {"text": "裂开",
     "frames": ["它双手抱头蹲下，嘴巴张成方形，眼睛变成两条竖线，头顶上方有一道裂纹",
                "它双手抱头蹲下，嘴巴张成方形，眼睛变成两条竖线，头顶上方有三道裂纹"]},
    {"text": "谢谢",
     "frames": ["它双手捧着一颗小红心放在胸前，眼睛弯成月牙，脸颊泛红",
                "它双手把一颗小红心举到头顶上方，眼睛弯成月牙，脸颊泛红"]},
    {"text": "在的",
     "frames": ["它举起一只前爪在身体左侧挥手，另一只爪垂在身侧，睁大眼睛微笑",
                "它把举起的前爪挥到身体右侧，另一只爪垂在身侧，睁大眼睛微笑"]},
]


def state_for(state, subject):
    """把默认动作句调成适合这类主角的说法。

    猫狗有前爪、有耳朵，人没有。同一套八组动作要能直接给任意主角复用，
    所以改字这一步放在拼提示词的时候做，词表本身留原样。
    """
    k = kind_of(subject)
    if k == KIND_ANIMAL:
        return state
    s = (state.replace("前爪", "手")
              .replace("另一只爪", "另一只手")
              .replace("爪", "手"))
    if k == KIND_PERSON:
        s = s.replace("，耳朵向后压", "").replace("它", "他")
    return s


def moods_from_texts(texts):
    """读者只改了文案时，动作句按位置沿用默认的八组。

    不做「按文案猜动作」—— 猜错的动作句会直接把画面带崩，
    宁可让读者改回默认文字，也不要交一张看不懂的图。
    """
    out = []
    for i, t in enumerate(texts[:len(DEFAULT_MOODS)]):
        base = DEFAULT_MOODS[i]
        out.append({"text": t.strip() or base["text"], "frames": list(base["frames"])})
    for base in DEFAULT_MOODS[len(out):]:
        out.append({"text": base["text"], "frames": list(base["frames"])})
    return out
