"""共享地名词表：Run 能力路由与计划工具策略的唯一事实源。

此前中文名单在 `agent_plan_tool_policy` 内、英文名单在 `run_capability_router` 内各维护
一份，两份互不同步（英文有 harbin/sanya，中文没有哈尔滨/三亚），导致同一个请求换语言
就换结果。

同一文件里放两份手写列表并不解决漂移：中英索引必须**派生自同一条记录**，否则只改一侧
仍然可能（PR #27 评审发现中文有邢台/承德、英文缺 xingtai/chengde）。因此城市是
`(省级单位, 中文名, 英文名)` 三元组，两侧索引与城市 ID 全部从记录派生，缺别名由测试拦住。

城市 ID 保留省级身份：`北京市朝阳区` 属于北京，`辽宁省朝阳市` 是朝阳，同名行政区不撞键。

词表只用于**提升置信度**：命中表示端点确定是地名；未命中不代表不是地名，是否公开出行
工具由出行意图强度决定（见 `agent_plan_tool_policy.resolve_product_capability_signals`）。
"""

from __future__ import annotations

# 中国地级及以上行政区（含直辖市、自治州、地区、盟）与常见境外城市。
# 每条记录自带中英文名，结构上无法只更新一侧。
_CITY_RECORDS: tuple[tuple[str, str, str], ...] = (
    # 直辖市
    ("直辖市", "北京", "beijing"),
    ("直辖市", "天津", "tianjin"),
    ("直辖市", "上海", "shanghai"),
    ("直辖市", "重庆", "chongqing"),
    # 河北
    ("河北", "石家庄", "shijiazhuang"),
    ("河北", "唐山", "tangshan"),
    ("河北", "秦皇岛", "qinhuangdao"),
    ("河北", "邯郸", "handan"),
    ("河北", "邢台", "xingtai"),
    ("河北", "保定", "baoding"),
    ("河北", "张家口", "zhangjiakou"),
    ("河北", "承德", "chengde"),
    ("河北", "沧州", "cangzhou"),
    ("河北", "廊坊", "langfang"),
    ("河北", "衡水", "hengshui"),
    # 山西
    ("山西", "太原", "taiyuan"),
    ("山西", "大同", "datong"),
    ("山西", "阳泉", "yangquan"),
    ("山西", "长治", "changzhi"),
    ("山西", "晋城", "jincheng"),
    ("山西", "朔州", "shuozhou"),
    ("山西", "晋中", "jinzhong"),
    ("山西", "运城", "yuncheng"),
    ("山西", "忻州", "xinzhou"),
    ("山西", "临汾", "linfen"),
    ("山西", "吕梁", "lvliang"),
    # 内蒙古
    ("内蒙古", "呼和浩特", "hohhot"),
    ("内蒙古", "包头", "baotou"),
    ("内蒙古", "乌海", "wuhai"),
    ("内蒙古", "赤峰", "chifeng"),
    ("内蒙古", "通辽", "tongliao"),
    ("内蒙古", "鄂尔多斯", "ordos"),
    ("内蒙古", "呼伦贝尔", "hulunbuir"),
    ("内蒙古", "巴彦淖尔", "bayannur"),
    ("内蒙古", "乌兰察布", "ulanqab"),
    ("内蒙古", "兴安", "hinggan"),
    ("内蒙古", "锡林郭勒", "xilingol"),
    ("内蒙古", "阿拉善", "alxa"),
    # 辽宁
    ("辽宁", "沈阳", "shenyang"),
    ("辽宁", "大连", "dalian"),
    ("辽宁", "鞍山", "anshan"),
    ("辽宁", "抚顺", "fushun"),
    ("辽宁", "本溪", "benxi"),
    ("辽宁", "丹东", "dandong"),
    ("辽宁", "锦州", "jinzhou"),
    ("辽宁", "营口", "yingkou"),
    ("辽宁", "阜新", "fuxin"),
    ("辽宁", "辽阳", "liaoyang"),
    ("辽宁", "盘锦", "panjin"),
    ("辽宁", "铁岭", "tieling"),
    ("辽宁", "朝阳", "chaoyang"),
    ("辽宁", "葫芦岛", "huludao"),
    # 吉林
    ("吉林", "长春", "changchun"),
    ("吉林", "吉林", "jilin"),
    ("吉林", "四平", "siping"),
    ("吉林", "辽源", "liaoyuan"),
    ("吉林", "通化", "tonghua"),
    ("吉林", "白山", "baishan"),
    ("吉林", "松原", "songyuan"),
    ("吉林", "白城", "baicheng"),
    ("吉林", "延边", "yanbian"),
    # 黑龙江
    ("黑龙江", "哈尔滨", "harbin"),
    ("黑龙江", "齐齐哈尔", "qiqihar"),
    ("黑龙江", "鸡西", "jixi"),
    ("黑龙江", "鹤岗", "hegang"),
    ("黑龙江", "双鸭山", "shuangyashan"),
    ("黑龙江", "大庆", "daqing"),
    ("黑龙江", "伊春", "yichun"),
    ("黑龙江", "佳木斯", "jiamusi"),
    ("黑龙江", "七台河", "qitaihe"),
    ("黑龙江", "牡丹江", "mudanjiang"),
    ("黑龙江", "黑河", "heihe"),
    ("黑龙江", "绥化", "suihua"),
    ("黑龙江", "大兴安岭", "daxinganling"),
    # 江苏
    ("江苏", "南京", "nanjing"),
    ("江苏", "无锡", "wuxi"),
    ("江苏", "徐州", "xuzhou"),
    ("江苏", "常州", "changzhou"),
    ("江苏", "苏州", "suzhou"),
    ("江苏", "南通", "nantong"),
    ("江苏", "连云港", "lianyungang"),
    ("江苏", "淮安", "huaian"),
    ("江苏", "盐城", "yancheng"),
    ("江苏", "扬州", "yangzhou"),
    ("江苏", "镇江", "zhenjiang"),
    ("江苏", "泰州", "taizhou"),
    ("江苏", "宿迁", "suqian"),
    # 浙江
    ("浙江", "杭州", "hangzhou"),
    ("浙江", "宁波", "ningbo"),
    ("浙江", "温州", "wenzhou"),
    ("浙江", "嘉兴", "jiaxing"),
    ("浙江", "湖州", "huzhou"),
    ("浙江", "绍兴", "shaoxing"),
    ("浙江", "金华", "jinhua"),
    ("浙江", "衢州", "quzhou"),
    ("浙江", "舟山", "zhoushan"),
    ("浙江", "台州", "taizhou-zj"),
    ("浙江", "丽水", "lishui"),
    # 安徽
    ("安徽", "合肥", "hefei"),
    ("安徽", "芜湖", "wuhu"),
    ("安徽", "蚌埠", "bengbu"),
    ("安徽", "淮南", "huainan"),
    ("安徽", "马鞍山", "maanshan"),
    ("安徽", "淮北", "huaibei"),
    ("安徽", "铜陵", "tongling"),
    ("安徽", "安庆", "anqing"),
    ("安徽", "黄山", "huangshan"),
    ("安徽", "滁州", "chuzhou"),
    ("安徽", "阜阳", "fuyang"),
    ("安徽", "宿州", "suzhou-ah"),
    ("安徽", "六安", "luan"),
    ("安徽", "亳州", "bozhou"),
    ("安徽", "池州", "chizhou"),
    ("安徽", "宣城", "xuancheng"),
    # 福建
    ("福建", "福州", "fuzhou"),
    ("福建", "厦门", "xiamen"),
    ("福建", "莆田", "putian"),
    ("福建", "三明", "sanming"),
    ("福建", "泉州", "quanzhou"),
    ("福建", "漳州", "zhangzhou"),
    ("福建", "南平", "nanping"),
    ("福建", "龙岩", "longyan"),
    ("福建", "宁德", "ningde"),
    # 江西
    ("江西", "南昌", "nanchang"),
    ("江西", "景德镇", "jingdezhen"),
    ("江西", "萍乡", "pingxiang"),
    ("江西", "九江", "jiujiang"),
    ("江西", "新余", "xinyu"),
    ("江西", "鹰潭", "yingtan"),
    ("江西", "赣州", "ganzhou"),
    ("江西", "吉安", "jian"),
    ("江西", "宜春", "yichun-jx"),
    ("江西", "抚州", "fuzhou-jx"),
    ("江西", "上饶", "shangrao"),
    # 山东
    ("山东", "济南", "jinan"),
    ("山东", "青岛", "qingdao"),
    ("山东", "淄博", "zibo"),
    ("山东", "枣庄", "zaozhuang"),
    ("山东", "东营", "dongying"),
    ("山东", "烟台", "yantai"),
    ("山东", "潍坊", "weifang"),
    ("山东", "济宁", "jining"),
    ("山东", "泰安", "taian"),
    ("山东", "威海", "weihai"),
    ("山东", "日照", "rizhao"),
    ("山东", "临沂", "linyi"),
    ("山东", "德州", "dezhou"),
    ("山东", "聊城", "liaocheng"),
    ("山东", "滨州", "binzhou"),
    ("山东", "菏泽", "heze"),
    # 河南
    ("河南", "郑州", "zhengzhou"),
    ("河南", "开封", "kaifeng"),
    ("河南", "洛阳", "luoyang"),
    ("河南", "平顶山", "pingdingshan"),
    ("河南", "安阳", "anyang"),
    ("河南", "鹤壁", "hebi"),
    ("河南", "新乡", "xinxiang"),
    ("河南", "焦作", "jiaozuo"),
    ("河南", "濮阳", "puyang"),
    ("河南", "许昌", "xuchang"),
    ("河南", "漯河", "luohe"),
    ("河南", "三门峡", "sanmenxia"),
    ("河南", "南阳", "nanyang"),
    ("河南", "商丘", "shangqiu"),
    ("河南", "信阳", "xinyang"),
    ("河南", "周口", "zhoukou"),
    ("河南", "驻马店", "zhumadian"),
    ("河南", "济源", "jiyuan"),
    # 湖北
    ("湖北", "武汉", "wuhan"),
    ("湖北", "黄石", "huangshi"),
    ("湖北", "十堰", "shiyan"),
    ("湖北", "宜昌", "yichang"),
    ("湖北", "襄阳", "xiangyang"),
    ("湖北", "鄂州", "ezhou"),
    ("湖北", "荆门", "jingmen"),
    ("湖北", "孝感", "xiaogan"),
    ("湖北", "荆州", "jingzhou"),
    ("湖北", "黄冈", "huanggang"),
    ("湖北", "咸宁", "xianning"),
    ("湖北", "随州", "suizhou"),
    ("湖北", "恩施", "enshi"),
    # 湖南
    ("湖南", "长沙", "changsha"),
    ("湖南", "株洲", "zhuzhou"),
    ("湖南", "湘潭", "xiangtan"),
    ("湖南", "衡阳", "hengyang"),
    ("湖南", "邵阳", "shaoyang"),
    ("湖南", "岳阳", "yueyang"),
    ("湖南", "常德", "changde"),
    ("湖南", "张家界", "zhangjiajie"),
    ("湖南", "益阳", "yiyang"),
    ("湖南", "郴州", "chenzhou"),
    ("湖南", "永州", "yongzhou"),
    ("湖南", "怀化", "huaihua"),
    ("湖南", "娄底", "loudi"),
    ("湖南", "湘西", "xiangxi"),
    # 广东
    ("广东", "广州", "guangzhou"),
    ("广东", "韶关", "shaoguan"),
    ("广东", "深圳", "shenzhen"),
    ("广东", "珠海", "zhuhai"),
    ("广东", "汕头", "shantou"),
    ("广东", "佛山", "foshan"),
    ("广东", "江门", "jiangmen"),
    ("广东", "湛江", "zhanjiang"),
    ("广东", "茂名", "maoming"),
    ("广东", "肇庆", "zhaoqing"),
    ("广东", "惠州", "huizhou"),
    ("广东", "梅州", "meizhou"),
    ("广东", "汕尾", "shanwei"),
    ("广东", "河源", "heyuan"),
    ("广东", "阳江", "yangjiang"),
    ("广东", "清远", "qingyuan"),
    ("广东", "东莞", "dongguan"),
    ("广东", "中山", "zhongshan"),
    ("广东", "潮州", "chaozhou"),
    ("广东", "揭阳", "jieyang"),
    ("广东", "云浮", "yunfu"),
    # 广西
    ("广西", "南宁", "nanning"),
    ("广西", "柳州", "liuzhou"),
    ("广西", "桂林", "guilin"),
    ("广西", "梧州", "wuzhou"),
    ("广西", "北海", "beihai"),
    ("广西", "防城港", "fangchenggang"),
    ("广西", "钦州", "qinzhou"),
    ("广西", "贵港", "guigang"),
    ("广西", "玉林", "yulin"),
    ("广西", "百色", "baise"),
    ("广西", "贺州", "hezhou"),
    ("广西", "河池", "hechi"),
    ("广西", "来宾", "laibin"),
    ("广西", "崇左", "chongzuo"),
    # 海南
    ("海南", "海口", "haikou"),
    ("海南", "三亚", "sanya"),
    ("海南", "三沙", "sansha"),
    ("海南", "儋州", "danzhou"),
    # 四川
    ("四川", "成都", "chengdu"),
    ("四川", "自贡", "zigong"),
    ("四川", "攀枝花", "panzhihua"),
    ("四川", "泸州", "luzhou"),
    ("四川", "德阳", "deyang"),
    ("四川", "绵阳", "mianyang"),
    ("四川", "广元", "guangyuan"),
    ("四川", "遂宁", "suining"),
    ("四川", "内江", "neijiang"),
    ("四川", "乐山", "leshan"),
    ("四川", "南充", "nanchong"),
    ("四川", "眉山", "meishan"),
    ("四川", "宜宾", "yibin"),
    ("四川", "广安", "guangan"),
    ("四川", "达州", "dazhou"),
    ("四川", "雅安", "yaan"),
    ("四川", "巴中", "bazhong"),
    ("四川", "资阳", "ziyang"),
    ("四川", "阿坝", "aba"),
    ("四川", "甘孜", "garze"),
    ("四川", "凉山", "liangshan"),
    # 贵州
    ("贵州", "贵阳", "guiyang"),
    ("贵州", "六盘水", "liupanshui"),
    ("贵州", "遵义", "zunyi"),
    ("贵州", "安顺", "anshun"),
    ("贵州", "毕节", "bijie"),
    ("贵州", "铜仁", "tongren"),
    ("贵州", "黔西南", "qianxinan"),
    ("贵州", "黔东南", "qiandongnan"),
    ("贵州", "黔南", "qiannan"),
    # 云南
    ("云南", "昆明", "kunming"),
    ("云南", "曲靖", "qujing"),
    ("云南", "玉溪", "yuxi"),
    ("云南", "保山", "baoshan"),
    ("云南", "昭通", "zhaotong"),
    ("云南", "丽江", "lijiang"),
    ("云南", "普洱", "puer"),
    ("云南", "临沧", "lincang"),
    ("云南", "楚雄", "chuxiong"),
    ("云南", "红河", "honghe"),
    ("云南", "文山", "wenshan"),
    ("云南", "西双版纳", "xishuangbanna"),
    ("云南", "大理", "dali"),
    ("云南", "德宏", "dehong"),
    ("云南", "怒江", "nujiang"),
    ("云南", "迪庆", "diqing"),
    # 西藏
    ("西藏", "拉萨", "lhasa"),
    ("西藏", "日喀则", "shigatse"),
    ("西藏", "昌都", "chamdo"),
    ("西藏", "林芝", "nyingchi"),
    ("西藏", "山南", "shannan"),
    ("西藏", "那曲", "nagqu"),
    ("西藏", "阿里", "ngari"),
    # 陕西
    ("陕西", "西安", "xian"),
    ("陕西", "铜川", "tongchuan"),
    ("陕西", "宝鸡", "baoji"),
    ("陕西", "咸阳", "xianyang"),
    ("陕西", "渭南", "weinan"),
    ("陕西", "延安", "yanan"),
    ("陕西", "汉中", "hanzhong"),
    ("陕西", "榆林", "yulin-sn"),
    ("陕西", "安康", "ankang"),
    ("陕西", "商洛", "shangluo"),
    # 甘肃
    ("甘肃", "兰州", "lanzhou"),
    ("甘肃", "嘉峪关", "jiayuguan"),
    ("甘肃", "金昌", "jinchang"),
    ("甘肃", "白银", "baiyin"),
    ("甘肃", "天水", "tianshui"),
    ("甘肃", "武威", "wuwei"),
    ("甘肃", "张掖", "zhangye"),
    ("甘肃", "平凉", "pingliang"),
    ("甘肃", "酒泉", "jiuquan"),
    ("甘肃", "庆阳", "qingyang"),
    ("甘肃", "定西", "dingxi"),
    ("甘肃", "陇南", "longnan"),
    ("甘肃", "临夏", "linxia"),
    ("甘肃", "甘南", "gannan"),
    # 青海
    ("青海", "西宁", "xining"),
    ("青海", "海东", "haidong"),
    ("青海", "海北", "haibei"),
    ("青海", "黄南", "huangnan"),
    ("青海", "果洛", "golog"),
    ("青海", "玉树", "yushu"),
    ("青海", "海西", "haixi"),
    # 宁夏
    ("宁夏", "银川", "yinchuan"),
    ("宁夏", "石嘴山", "shizuishan"),
    ("宁夏", "吴忠", "wuzhong"),
    ("宁夏", "固原", "guyuan"),
    ("宁夏", "中卫", "zhongwei"),
    # 新疆
    ("新疆", "乌鲁木齐", "urumqi"),
    ("新疆", "克拉玛依", "karamay"),
    ("新疆", "吐鲁番", "turpan"),
    ("新疆", "哈密", "hami"),
    ("新疆", "昌吉", "changji"),
    ("新疆", "博尔塔拉", "bortala"),
    ("新疆", "巴音郭楞", "bayingolin"),
    ("新疆", "阿克苏", "aksu"),
    ("新疆", "克孜勒苏", "kizilsu"),
    ("新疆", "喀什", "kashgar"),
    ("新疆", "和田", "hotan"),
    ("新疆", "伊犁", "yili"),
    ("新疆", "塔城", "tacheng"),
    ("新疆", "阿勒泰", "altay"),
    ("新疆", "石河子", "shihezi"),
    # 港澳台
    ("港澳台", "香港", "hong kong"),
    ("港澳台", "澳门", "macau"),
    ("港澳台", "台北", "taipei"),
    ("港澳台", "新北", "new taipei"),
    ("港澳台", "桃园", "taoyuan"),
    ("港澳台", "台中", "taichung"),
    ("港澳台", "台南", "tainan"),
    ("港澳台", "高雄", "kaohsiung"),
    ("港澳台", "基隆", "keelung"),
    ("港澳台", "新竹", "hsinchu"),
    # 境外
    ("境外", "东京", "tokyo"),
    ("境外", "大阪", "osaka"),
    ("境外", "京都", "kyoto"),
    ("境外", "名古屋", "nagoya"),
    ("境外", "札幌", "sapporo"),
    ("境外", "福冈", "fukuoka"),
    ("境外", "冲绳", "okinawa"),
    ("境外", "首尔", "seoul"),
    ("境外", "釜山", "busan"),
    ("境外", "济州", "jeju"),
    ("境外", "曼谷", "bangkok"),
    ("境外", "清迈", "chiang mai"),
    ("境外", "普吉", "phuket"),
    ("境外", "芭提雅", "pattaya"),
    ("境外", "新加坡", "singapore"),
    ("境外", "吉隆坡", "kuala lumpur"),
    ("境外", "槟城", "penang"),
    ("境外", "雅加达", "jakarta"),
    ("境外", "巴厘岛", "bali"),
    ("境外", "马尼拉", "manila"),
    ("境外", "河内", "hanoi"),
    ("境外", "岘港", "da nang"),
    ("境外", "胡志明", "ho chi minh"),
    ("境外", "金边", "phnom penh"),
    ("境外", "万象", "vientiane"),
    ("境外", "仰光", "yangon"),
    ("境外", "新德里", "new delhi"),
    ("境外", "孟买", "mumbai"),
    ("境外", "班加罗尔", "bangalore"),
    ("境外", "加德满都", "kathmandu"),
    ("境外", "科伦坡", "colombo"),
    ("境外", "达卡", "dhaka"),
    ("境外", "伊斯兰堡", "islamabad"),
    ("境外", "迪拜", "dubai"),
    ("境外", "多哈", "doha"),
    ("境外", "伊斯坦布尔", "istanbul"),
    ("境外", "特拉维夫", "tel aviv"),
    ("境外", "开罗", "cairo"),
    ("境外", "约翰内斯堡", "johannesburg"),
    ("境外", "内罗毕", "nairobi"),
    ("境外", "莫斯科", "moscow"),
    ("境外", "圣彼得堡", "saint petersburg"),
    ("境外", "伦敦", "london"),
    ("境外", "曼彻斯特", "manchester"),
    ("境外", "爱丁堡", "edinburgh"),
    ("境外", "都柏林", "dublin"),
    ("境外", "巴黎", "paris"),
    ("境外", "尼斯", "nice"),
    ("境外", "柏林", "berlin"),
    ("境外", "慕尼黑", "munich"),
    ("境外", "法兰克福", "frankfurt"),
    ("境外", "汉堡", "hamburg"),
    ("境外", "阿姆斯特丹", "amsterdam"),
    ("境外", "布鲁塞尔", "brussels"),
    ("境外", "苏黎世", "zurich"),
    ("境外", "日内瓦", "geneva"),
    ("境外", "维也纳", "vienna"),
    ("境外", "布拉格", "prague"),
    ("境外", "布达佩斯", "budapest"),
    ("境外", "华沙", "warsaw"),
    ("境外", "罗马", "rome"),
    ("境外", "米兰", "milan"),
    ("境外", "威尼斯", "venice"),
    ("境外", "佛罗伦萨", "florence"),
    ("境外", "巴塞罗那", "barcelona"),
    ("境外", "马德里", "madrid"),
    ("境外", "里斯本", "lisbon"),
    ("境外", "雅典", "athens"),
    ("境外", "斯德哥尔摩", "stockholm"),
    ("境外", "奥斯陆", "oslo"),
    ("境外", "哥本哈根", "copenhagen"),
    ("境外", "赫尔辛基", "helsinki"),
    ("境外", "雷克雅未克", "reykjavik"),
    ("境外", "纽约", "new york"),
    ("境外", "洛杉矶", "los angeles"),
    ("境外", "旧金山", "san francisco"),
    ("境外", "芝加哥", "chicago"),
    ("境外", "西雅图", "seattle"),
    ("境外", "波士顿", "boston"),
    ("境外", "华盛顿", "washington"),
    ("境外", "拉斯维加斯", "las vegas"),
    ("境外", "迈阿密", "miami"),
    ("境外", "檀香山", "honolulu"),
    ("境外", "温哥华", "vancouver"),
    ("境外", "多伦多", "toronto"),
    ("境外", "蒙特利尔", "montreal"),
    ("境外", "墨西哥城", "mexico city"),
    ("境外", "圣保罗", "sao paulo"),
    ("境外", "里约热内卢", "rio de janeiro"),
    ("境外", "布宜诺斯艾利斯", "buenos aires"),
    ("境外", "利马", "lima"),
    ("境外", "圣地亚哥", "santiago"),
    ("境外", "悉尼", "sydney"),
    ("境外", "墨尔本", "melbourne"),
    ("境外", "布里斯班", "brisbane"),
    ("境外", "珀斯", "perth"),
    ("境外", "堪培拉", "canberra"),
    ("境外", "奥克兰", "auckland"),
    ("境外", "惠灵顿", "wellington"),
)

# 省级单位名：用于从 `广东省佛山市` 这类写法里剥出所属省份。
PROVINCE_NAMES: frozenset[str] = frozenset(
    {
        "北京",
        "天津",
        "上海",
        "重庆",
        "河北",
        "山西",
        "内蒙古",
        "辽宁",
        "吉林",
        "黑龙江",
        "江苏",
        "浙江",
        "安徽",
        "福建",
        "江西",
        "山东",
        "河南",
        "湖北",
        "湖南",
        "广东",
        "广西",
        "海南",
        "四川",
        "贵州",
        "云南",
        "西藏",
        "陕西",
        "甘肃",
        "青海",
        "宁夏",
        "新疆",
        "香港",
        "澳门",
        "台湾",
    }
)

_CITY_ID_BY_ZH: dict[str, list[str]] = {}
_CITY_ID_BY_EN: dict[str, str] = {}
for _province, _zh, _en in _CITY_RECORDS:
    _city_id = f"{_province}/{_zh}"
    _CITY_ID_BY_ZH.setdefault(_zh, []).append(_city_id)
    _CITY_ID_BY_EN[_en] = _city_id

KNOWN_LOCATION_NAMES: frozenset[str] = frozenset(_CITY_ID_BY_ZH)
EN_KNOWN_LOCATION_NAMES: frozenset[str] = frozenset(_CITY_ID_BY_EN)

# 中文知名地标：与城市名一起构成"确定是地点"的正向信号，但不参与跨城判定。
KNOWN_LANDMARK_NAMES: frozenset[str] = frozenset(
    {
        "故宫",
        "天安门",
        "颐和园",
        "长城",
        "圆明园",
        "鸟巢",
        "水立方",
        "外滩",
        "东方明珠",
        "迪士尼",
        "陆家嘴",
        "南京路",
        "西湖",
        "兵马俑",
        "大雁塔",
        "黄鹤楼",
        "岳麓山",
        "珠江新城",
        "奥体中心",
        "市民中心",
        "会展中心",
        "环球影城",
    }
)

EN_KNOWN_LANDMARK_NAMES: frozenset[str] = frozenset(
    {
        "the bund",
        "the forbidden city",
        "forbidden city",
        "tiananmen",
        "the summer palace",
        "summer palace",
        "the great wall",
        "great wall",
        "people's square",
        "peoples square",
        "lujiazui",
        "disneyland",
        "west lake",
        "terracotta army",
        "hongqiao station",
        "shanghai hongqiao station",
        "hongqiao airport",
        "pudong airport",
        "grand central station",
        "city hall",
        "downtown",
        "city center",
    }
)

# 行政区后缀：`北京市` 与 `北京` 必须等价。
ADMIN_DIVISION_SUFFIXES: tuple[str, ...] = ("省", "市", "区", "县", "镇", "乡", "街道", "自治州", "地区", "盟")

# 市辖下级行政区后缀：`北京市朝阳区` 的城市是北京，不是朝阳。
_SUB_CITY_SUFFIXES: tuple[str, ...] = ("区", "县", "镇", "乡", "街道", "新区", "开发区")

# 交通枢纽后缀：只有这些后缀才允许把 `广州南站` 归到 `广州`，`北京大学` 不在其列。
_TRANSPORT_HUB_SUFFIXES: tuple[str, ...] = ("站", "机场", "码头", "港", "客运站", "航站楼", "枢纽")


def _city_ids_for_zh(name: str) -> list[str]:
    return _CITY_ID_BY_ZH.get(name, [])


def _unique_city_id(name: str, province: str | None = None) -> str | None:
    """把中文城市名解析为城市 ID；同名跨省时必须由省份消歧。"""

    city_ids = _city_ids_for_zh(name)
    if not city_ids:
        return None
    if province is not None:
        for city_id in city_ids:
            if city_id.startswith(f"{province}/"):
                return city_id
        return None
    if len(city_ids) == 1:
        return city_ids[0]
    # 同名跨省且无省份线索：返回不带省份的弱键，两个同样含糊的名字才会相等。
    return f"?/{name}"


def normalize_location_name(value: str) -> str:
    """剥掉行政区后缀与上级行政区前缀，得到可用于词表比对的裸地名。"""

    resolved = _parse_city(value)
    if resolved is not None:
        return resolved[1]
    normalized = value.strip()
    for suffix in ADMIN_DIVISION_SUFFIXES:
        candidate = normalized.removesuffix(suffix)
        if candidate and candidate in KNOWN_LOCATION_NAMES:
            return candidate
    return normalized


def _parse_city(value: str) -> tuple[str, str] | None:
    """把一个端点槽位解析为 (城市 ID, 城市中文名)；解析不出来返回 None。"""

    text = value.strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in _CITY_ID_BY_EN:
        city_id = _CITY_ID_BY_EN[lowered]
        return city_id, city_id.split("/", 1)[1]

    # 先按"城市 + 下级行政区/交通枢纽"匹配原文：直辖市既是省级也是城市，
    # `北京市朝阳区` 必须归到北京，不能先当成省份剥掉再去找城市"朝阳"。
    direct = _city_with_subordinate_part(text, province=None)
    if direct is not None:
        return direct

    # 形如 `<省><城市>市`：剥省级前缀，省份同时用于同名城市消歧。
    province, body = _split_province_prefix(text)
    if province is None:
        return None
    nested = _city_with_subordinate_part(body, province=province)
    if nested is not None:
        return nested
    tail = body.removesuffix("市")
    if tail and tail in KNOWN_LOCATION_NAMES:
        city_id = _unique_city_id(tail, province)
        return (city_id, tail) if city_id else None
    return None


def _split_province_prefix(text: str) -> tuple[str | None, str]:
    for name in sorted(PROVINCE_NAMES, key=len, reverse=True):
        for prefix in (f"{name}省", f"{name}自治区", f"{name}市", name):
            if text.startswith(prefix) and len(text) > len(prefix):
                return name, text[len(prefix) :]
    return None, text


def _city_with_subordinate_part(body: str, *, province: str | None) -> tuple[str, str] | None:
    """匹配 `<城市>`、`<城市>市`、`<城市>[市]<下级区>`、`<城市><交通枢纽>`。"""

    for city_name in _longest_known_prefixes(body):
        rest = body[len(city_name) :]
        if (
            rest not in ("", "市")
            and not rest.removeprefix("市").endswith(_SUB_CITY_SUFFIXES)
            and not rest.endswith(_TRANSPORT_HUB_SUFFIXES)
        ):
            continue
        if rest.removeprefix("市") == "" and rest != "" and rest != "市":
            continue
        city_id = _unique_city_id(city_name, province)
        if city_id is not None:
            return city_id, city_name
    return None


def _longest_known_prefixes(value: str):
    """按长度从长到短产出作为前缀出现的已知城市名。"""

    candidates = [name for name in KNOWN_LOCATION_NAMES if value.startswith(name)]
    return sorted(candidates, key=len, reverse=True)


def is_known_city_name(value: str) -> bool:
    """只判定城市级地名；地标、车站、机场不算。

    区分跨城与同城出行时必须用它而不是 `is_known_location_name`：
    `上海虹桥站` 与 `外滩` 都是确定地点，但两者之间是同城路线。
    """

    return _parse_city(value) is not None


def is_known_location_name(value: str) -> bool:
    """中英文统一入口：命中城市或地标词表即视为确定地名。"""

    if not value:
        return False
    if _parse_city(value) is not None:
        return True
    normalized = value.strip()
    if normalized in KNOWN_LANDMARK_NAMES:
        return True
    return normalized.lower() in EN_KNOWN_LANDMARK_NAMES


def resolve_city_key(value: str) -> str | None:
    """取出端点所属的城市 ID；无法确定时返回 None。

    ID 带省级前缀，因此 `北京市朝阳区`（北京）与 `辽宁省朝阳市`（朝阳）不会撞键。
    """

    resolved = _parse_city(value)
    return resolved[0] if resolved is not None else None


def are_distinct_known_cities(origin: str, destination: str) -> bool:
    """两端都能定位到已知城市且城市不同时，才算跨城出行。"""

    origin_city = resolve_city_key(origin)
    destination_city = resolve_city_key(destination)
    if origin_city is None or destination_city is None:
        return False
    return origin_city != destination_city


def has_known_location_prefix(value: str) -> bool:
    """允许 `上海外滩`、`浦东新区外滩` 这类带前缀的地标写法。"""

    for landmark in KNOWN_LANDMARK_NAMES:
        if value == landmark or not value.endswith(landmark):
            continue
        prefix = value[: -len(landmark)]
        if prefix in KNOWN_LOCATION_NAMES or prefix.endswith(ADMIN_DIVISION_SUFFIXES):
            return True
    return False
