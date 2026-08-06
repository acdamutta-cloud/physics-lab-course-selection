"""通用高校物理实验的演示器材与实验室配置。

这些数据用于可信的演示和排课约束验证，不代表任何学校的真实资产台账。
耗材和环境要求写入项目备注，不作为可重复使用的库存器材。
"""

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class EquipmentSpec:
    code: str
    name: str
    model: str
    unit: str = "台"


@dataclass(frozen=True)
class RequirementSpec:
    equipment_code: str
    units_per_group: int = 1
    required: bool = True


@dataclass(frozen=True)
class ProjectResourceSpec:
    requirements: tuple[RequirementSpec, ...]
    lab_codes: tuple[str, ...]
    material_note: str | None = None
    capability_note: str | None = None


@dataclass(frozen=True)
class LaboratorySpec:
    code: str
    name: str
    room_type: str
    safety_capacity: int


def requirement(
    equipment_code: str,
    units_per_group: int = 1,
    *,
    required: bool = True,
) -> RequirementSpec:
    return RequirementSpec(equipment_code, units_per_group, required)


EQUIPMENT_SPECS = (
    EquipmentSpec("PHY-CALIPER", "游标卡尺", "0-150mm", "把"),
    EquipmentSpec("PHY-MICROMETER", "螺旋测微计", "0-25mm", "把"),
    EquipmentSpec("PHY-BALANCE", "电子天平", "FA2004"),
    EquipmentSpec("PHY-PENDULUM", "单摆实验装置", "DP-2024", "套"),
    EquipmentSpec("PHY-METER-RULER", "米尺", "1m", "把"),
    EquipmentSpec("PHY-STOPWATCH", "电子秒表", "0.01s", "只"),
    EquipmentSpec("PHY-AIR-TRACK", "气垫导轨实验系统", "QG-5", "套"),
    EquipmentSpec("PHY-PHOTOGATE", "光电计时系统", "GD-8A", "套"),
    EquipmentSpec("PHY-YOUNG", "杨氏模量测定仪", "YM-3A", "套"),
    EquipmentSpec("PHY-TELESCOPE-SCALE", "望远镜附标尺", "YM-RULER", "套"),
    EquipmentSpec("PHY-SURFACE", "液体表面张力测定仪", "BZY-1", "套"),
    EquipmentSpec("PHY-SPECIFIC-HEAT", "金属比热容实验仪", "RZ-BR", "套"),
    EquipmentSpec("PHY-THERMOMETER", "数字温度计", "PT100", "支"),
    EquipmentSpec("PHY-OSCILLOSCOPE", "数字示波器", "TBS1102C"),
    EquipmentSpec("PHY-SIGNAL-GEN", "信号发生器", "DG1022Z"),
    EquipmentSpec("PHY-CIRCUIT-MODULE", "示波器实验电路模块", "OSC-M1", "套"),
    EquipmentSpec("PHY-DC-SUPPLY", "直流稳压电源", "DP832"),
    EquipmentSpec("PHY-MULTIMETER", "数字万用表", "UT61E+"),
    EquipmentSpec("PHY-RESISTOR-MODULE", "伏安法电阻实验模块", "VA-R1", "套"),
    EquipmentSpec("PHY-HALL", "霍尔效应实验仪", "HZS-II", "套"),
    EquipmentSpec("PHY-RLC", "RLC暂态实验模块", "RLC-M1", "套"),
    EquipmentSpec("PHY-AC-BRIDGE", "交流电桥实验仪", "ACB-2", "套"),
    EquipmentSpec("PHY-SENSOR", "传感器特性实验仪", "CSY-998", "套"),
    EquipmentSpec("PHY-ULTRASOUND", "超声波声速测量仪", "SV-DH", "套"),
    EquipmentSpec("PHY-THERMOCOUPLE", "热电偶定标实验仪", "TC-2", "套"),
    EquipmentSpec("PHY-SOLAR-CELL", "太阳能电池特性实验仪", "SEC-A", "套"),
    EquipmentSpec("PHY-VARIABLE-LAMP", "可调光源", "LED-V1", "套"),
    EquipmentSpec("PHY-OPTICAL-BENCH", "光具座", "GJZ-2", "套"),
    EquipmentSpec("PHY-LENS-KIT", "薄透镜成像组件", "LENS-SET", "套"),
    EquipmentSpec("PHY-HENE-LASER", "氦氖激光器", "HN-250"),
    EquipmentSpec("PHY-DIFFRACTION-KIT", "干涉衍射组件", "ID-SET", "套"),
    EquipmentSpec("PHY-FIBER", "光纤传输实验系统", "OF-3", "套"),
    EquipmentSpec("PHY-OPTICAL-METER", "光功率计", "OPM-2"),
    EquipmentSpec("PHY-VIBRATION", "受迫振动与共振实验仪", "FD-VR", "套"),
    EquipmentSpec("PHY-DAQ", "数据采集器", "DAQ-8", "套"),
    EquipmentSpec("PHY-VACUUM", "真空获得实验系统", "VTS-2", "套"),
    EquipmentSpec("PHY-VACUUM-GAUGE", "复合真空计", "ZDF-III", "套"),
    EquipmentSpec("PHY-PHOTOELECTRIC", "光电效应实验仪", "ZKY-GD-4", "套"),
    EquipmentSpec("PHY-MERCURY-LAMP", "汞灯电源", "GY-6", "套"),
    EquipmentSpec("PHY-MICROCURRENT", "微电流测量仪", "EM-5"),
    EquipmentSpec("PHY-FRANCK-HERTZ", "弗兰克-赫兹实验仪", "FH-2", "套"),
    EquipmentSpec("PHY-MILLIKAN", "密立根油滴实验仪", "MOD-5", "套"),
    EquipmentSpec("PHY-E-DIFFRACTION", "电子衍射实验仪", "ED-2", "套"),
    EquipmentSpec("PHY-HV-SUPPLY", "高压稳压电源", "HV-5K"),
    EquipmentSpec("PHY-ZEEMAN", "塞曼效应实验系统", "ZE-3", "套"),
    EquipmentSpec("PHY-NMR", "核磁共振实验仪", "NMR-20", "套"),
    EquipmentSpec("PHY-MICROWAVE", "微波布拉格衍射系统", "MW-3", "套"),
    EquipmentSpec("PHY-HOLOGRAPHY", "全息照相光学平台", "HOLO-2", "套"),
    EquipmentSpec("PHY-SINGLE-PHOTON", "单光子计数实验仪", "SPC-3", "套"),
    EquipmentSpec("PHY-STABLE-LIGHT", "稳光光源", "SLS-1", "套"),
    EquipmentSpec("PHY-XRAY", "X射线实验仪", "XR-4", "套"),
    EquipmentSpec("PHY-XRAY-SPECTROMETER", "X射线谱仪与计数器", "XRS-4", "套"),
)

EQUIPMENT_BY_CODE = {item.code: item for item in EQUIPMENT_SPECS}
RESERVE_GROUPS = 2

LABORATORY_SPECS = (
    LaboratorySpec("A201", "基础力学实验室 A201", "基础力学", 24),
    LaboratorySpec("A202", "基础力学实验室 A202", "基础力学", 24),
    LaboratorySpec("A203", "基础力学实验室 A203", "基础力学", 24),
    LaboratorySpec("B101", "电学综合实验室 B101", "电学综合", 20),
    LaboratorySpec("B102", "电学综合实验室 B102", "电学综合", 20),
    LaboratorySpec("B201", "光学实验室 B201", "光学", 20),
    LaboratorySpec("B202", "光学实验室 B202", "光学", 20),
    LaboratorySpec("C301", "近代物理实验室 C301", "近代物理", 16),
    LaboratorySpec("C302", "近代物理实验室 C302", "近代物理", 16),
    LaboratorySpec("D101", "综合实验室 D101", "综合", 30),
    LaboratorySpec("D102", "综合实验室 D102", "综合", 30),
)


PROJECT_RESOURCE_SPECS: dict[str, ProjectResourceSpec] = {
    "DEMO-PHY101-P01": ProjectResourceSpec(
        (requirement("PHY-CALIPER"), requirement("PHY-MICROMETER"), requirement("PHY-BALANCE")),
        ("D101",),
        "被测圆柱体、钢球等样品按实验批次准备。",
    ),
    "DEMO-PHY101-P02": ProjectResourceSpec(
        (requirement("PHY-PENDULUM"), requirement("PHY-METER-RULER"), requirement("PHY-STOPWATCH")),
        ("A201",),
    ),
    "DEMO-PHY101-P03": ProjectResourceSpec(
        (requirement("PHY-AIR-TRACK"), requirement("PHY-PHOTOGATE")),
        ("A202",),
        "滑块、碰撞附件随气垫导轨实验系统成套配置。",
    ),
    "DEMO-PHY101-P04": ProjectResourceSpec(
        (
            requirement("PHY-YOUNG"),
            requirement("PHY-MICROMETER"),
            requirement("PHY-TELESCOPE-SCALE"),
        ),
        ("A203",),
        "金属丝和标准砝码随实验批次检查、补充。",
    ),
    "DEMO-PHY101-P05": ProjectResourceSpec(
        (requirement("PHY-SURFACE"), requirement("PHY-CALIPER")),
        ("D101",),
        "待测液体和清洗材料按实验批次准备。",
    ),
    "DEMO-PHY101-P06": ProjectResourceSpec(
        (
            requirement("PHY-SPECIFIC-HEAT"),
            requirement("PHY-BALANCE"),
            requirement("PHY-THERMOMETER"),
        ),
        ("D102",),
        "金属样品和实验用水按实验批次准备。",
    ),
    "DEMO-PHY101-P07": ProjectResourceSpec(
        (
            requirement("PHY-OSCILLOSCOPE"),
            requirement("PHY-SIGNAL-GEN"),
            requirement("PHY-CIRCUIT-MODULE"),
        ),
        ("B101",),
    ),
    "DEMO-PHY101-P08": ProjectResourceSpec(
        (
            requirement("PHY-DC-SUPPLY"),
            requirement("PHY-MULTIMETER", 2),
            requirement("PHY-RESISTOR-MODULE"),
        ),
        ("B101",),
    ),
    "DEMO-PHY101-P09": ProjectResourceSpec(
        (requirement("PHY-OPTICAL-BENCH"), requirement("PHY-LENS-KIT")),
        ("B201",),
    ),
    "DEMO-PHY101-P10": ProjectResourceSpec(
        (
            requirement("PHY-HENE-LASER"),
            requirement("PHY-OPTICAL-BENCH"),
            requirement("PHY-DIFFRACTION-KIT"),
        ),
        ("B201", "B202"),
        "光栅、双缝、单缝和观察屏随干涉衍射组件成套配置。",
    ),
    "DEMO-PHY201-P01": ProjectResourceSpec(
        (requirement("PHY-HALL"),),
        ("B102",),
    ),
    "DEMO-PHY201-P02": ProjectResourceSpec(
        (
            requirement("PHY-RLC"),
            requirement("PHY-SIGNAL-GEN"),
            requirement("PHY-OSCILLOSCOPE"),
        ),
        ("B101", "B102"),
    ),
    "DEMO-PHY201-P03": ProjectResourceSpec(
        (requirement("PHY-AC-BRIDGE"), requirement("PHY-SIGNAL-GEN")),
        ("B102",),
    ),
    "DEMO-PHY201-P04": ProjectResourceSpec(
        (
            requirement("PHY-SENSOR"),
            requirement("PHY-DC-SUPPLY"),
            requirement("PHY-MULTIMETER"),
        ),
        ("B101",),
    ),
    "DEMO-PHY201-P05": ProjectResourceSpec(
        (requirement("PHY-ULTRASOUND"), requirement("PHY-OSCILLOSCOPE")),
        ("D102",),
    ),
    "DEMO-PHY201-P06": ProjectResourceSpec(
        (
            requirement("PHY-THERMOCOUPLE"),
            requirement("PHY-THERMOMETER"),
            requirement("PHY-MULTIMETER"),
        ),
        ("D102",),
        "冰水、沸水等温度参考介质按实验批次准备。",
    ),
    "DEMO-PHY201-P07": ProjectResourceSpec(
        (
            requirement("PHY-SOLAR-CELL"),
            requirement("PHY-VARIABLE-LAMP"),
            requirement("PHY-MULTIMETER", 2),
        ),
        ("B101",),
    ),
    "DEMO-PHY201-P08": ProjectResourceSpec(
        (requirement("PHY-FIBER"), requirement("PHY-OPTICAL-METER")),
        ("B201",),
        "实验光纤跳线作为易损附件按批次检查。",
    ),
    "DEMO-PHY201-P09": ProjectResourceSpec(
        (requirement("PHY-VIBRATION"), requirement("PHY-DAQ")),
        ("A203",),
    ),
    "DEMO-PHY201-P10": ProjectResourceSpec(
        (requirement("PHY-VACUUM"), requirement("PHY-VACUUM-GAUGE")),
        ("C301",),
        "真空密封件和泵油按维护周期检查。",
    ),
    "DEMO-PHY301-P01": ProjectResourceSpec(
        (
            requirement("PHY-PHOTOELECTRIC"),
            requirement("PHY-MERCURY-LAMP"),
            requirement("PHY-MICROCURRENT"),
        ),
        ("C301",),
    ),
    "DEMO-PHY301-P02": ProjectResourceSpec(
        (requirement("PHY-FRANCK-HERTZ"), requirement("PHY-OSCILLOSCOPE")),
        ("C302",),
    ),
    "DEMO-PHY301-P03": ProjectResourceSpec(
        (requirement("PHY-MILLIKAN"),),
        ("C302",),
        "油滴用油按实验批次准备。",
    ),
    "DEMO-PHY301-P04": ProjectResourceSpec(
        (requirement("PHY-E-DIFFRACTION"), requirement("PHY-HV-SUPPLY")),
        ("C302",),
        capability_note="仅限具备高压实验防护条件的实验室。",
    ),
    "DEMO-PHY301-P05": ProjectResourceSpec(
        (requirement("PHY-ZEEMAN"),),
        ("C301",),
        capability_note="实验室须具备稳固光学平台和遮光条件。",
    ),
    "DEMO-PHY301-P06": ProjectResourceSpec(
        (requirement("PHY-NMR"), requirement("PHY-OSCILLOSCOPE")),
        ("C302",),
        capability_note="实验室须满足磁场设备安全距离要求。",
    ),
    "DEMO-PHY301-P07": ProjectResourceSpec(
        (requirement("PHY-MICROWAVE"),),
        ("C302",),
    ),
    "DEMO-PHY301-P08": ProjectResourceSpec(
        (
            requirement("PHY-HOLOGRAPHY"),
            requirement("PHY-HENE-LASER"),
        ),
        ("B202",),
        "全息干板和显影材料按实验批次准备。",
        "仅限具备暗室、激光防护和防振条件的实验室。",
    ),
    "DEMO-PHY301-P09": ProjectResourceSpec(
        (
            requirement("PHY-SINGLE-PHOTON"),
            requirement("PHY-STABLE-LIGHT"),
            requirement("PHY-OSCILLOSCOPE"),
        ),
        ("C301",),
        capability_note="实验室须具备遮光和弱光测量条件。",
    ),
    "DEMO-PHY301-P10": ProjectResourceSpec(
        (requirement("PHY-XRAY"), requirement("PHY-XRAY-SPECTROMETER")),
        ("C302",),
        capability_note="仅限完成辐射安全评估并具备联锁防护的实验室。",
    ),
}

# 项目能力容量是教学核定值，不随备用器材数量自动扩大。
PROJECT_LAB_CAPACITIES: dict[str, dict[str, int]] = {
    "DEMO-PHY101-P01": {"D101": 12},
    "DEMO-PHY101-P02": {"A201": 24},
    "DEMO-PHY101-P03": {"A202": 12},
    "DEMO-PHY101-P04": {"A203": 24},
    "DEMO-PHY101-P05": {"D101": 16},
    "DEMO-PHY101-P06": {"D102": 16},
    "DEMO-PHY101-P07": {"B101": 20},
    "DEMO-PHY101-P08": {"B101": 20},
    "DEMO-PHY101-P09": {"B201": 20},
    "DEMO-PHY101-P10": {"B201": 20, "B202": 16},
    "DEMO-PHY201-P01": {"B102": 20},
    "DEMO-PHY201-P02": {"B101": 20, "B102": 18},
    "DEMO-PHY201-P03": {"B102": 20},
    "DEMO-PHY201-P04": {"B101": 20},
    "DEMO-PHY201-P05": {"D102": 16},
    "DEMO-PHY201-P06": {"D102": 16},
    "DEMO-PHY201-P07": {"B101": 16},
    "DEMO-PHY201-P08": {"B201": 16},
    "DEMO-PHY201-P09": {"A203": 12},
    "DEMO-PHY201-P10": {"C301": 8},
    "DEMO-PHY301-P01": {"C301": 14},
    "DEMO-PHY301-P02": {"C302": 8},
    "DEMO-PHY301-P03": {"C302": 16},
    "DEMO-PHY301-P04": {"C302": 8},
    "DEMO-PHY301-P05": {"C301": 8},
    "DEMO-PHY301-P06": {"C302": 8},
    "DEMO-PHY301-P07": {"C302": 8},
    "DEMO-PHY301-P08": {"B202": 8},
    "DEMO-PHY301-P09": {"C301": 8},
    "DEMO-PHY301-P10": {"C302": 4},
}


# 数量表示单间实验室的总数与可用数。未列出的既有库存不会被同步脚本删除。
LAB_INVENTORY_SPECS: dict[str, dict[str, tuple[int, int]]] = {
    "A201": {
        "PHY-PENDULUM": (12, 12),
        "PHY-METER-RULER": (12, 12),
        "PHY-STOPWATCH": (12, 12),
    },
    "A202": {
        "PHY-AIR-TRACK": (6, 6),
        "PHY-PHOTOGATE": (6, 6),
    },
    "A203": {
        "PHY-YOUNG": (12, 12),
        "PHY-MICROMETER": (12, 12),
        "PHY-TELESCOPE-SCALE": (12, 12),
        "PHY-VIBRATION": (6, 6),
        "PHY-DAQ": (6, 6),
    },
    "B101": {
        "PHY-OSCILLOSCOPE": (10, 10),
        "PHY-SIGNAL-GEN": (10, 10),
        "PHY-CIRCUIT-MODULE": (10, 10),
        "PHY-DC-SUPPLY": (10, 10),
        "PHY-MULTIMETER": (20, 20),
        "PHY-RESISTOR-MODULE": (10, 10),
        "PHY-RLC": (10, 10),
        "PHY-SENSOR": (10, 10),
        "PHY-SOLAR-CELL": (8, 8),
        "PHY-VARIABLE-LAMP": (8, 8),
    },
    "B102": {
        "PHY-HALL": (10, 10),
        "PHY-RLC": (9, 9),
        "PHY-SIGNAL-GEN": (10, 10),
        "PHY-OSCILLOSCOPE": (9, 9),
        "PHY-AC-BRIDGE": (10, 10),
    },
    "B201": {
        "PHY-OPTICAL-BENCH": (10, 10),
        "PHY-LENS-KIT": (10, 10),
        "PHY-HENE-LASER": (10, 10),
        "PHY-DIFFRACTION-KIT": (10, 10),
        "PHY-FIBER": (8, 8),
        "PHY-OPTICAL-METER": (8, 8),
    },
    "B202": {
        "PHY-HENE-LASER": (8, 8),
        "PHY-OPTICAL-BENCH": (8, 8),
        "PHY-DIFFRACTION-KIT": (8, 8),
        "PHY-HOLOGRAPHY": (4, 4),
    },
    "C301": {
        "PHY-VACUUM": (4, 4),
        "PHY-VACUUM-GAUGE": (4, 4),
        "PHY-PHOTOELECTRIC": (8, 8),
        "PHY-MERCURY-LAMP": (8, 8),
        "PHY-MICROCURRENT": (7, 7),
        "PHY-ZEEMAN": (4, 4),
        "PHY-SINGLE-PHOTON": (4, 4),
        "PHY-STABLE-LIGHT": (4, 4),
        "PHY-OSCILLOSCOPE": (4, 4),
    },
    "C302": {
        "PHY-FRANCK-HERTZ": (8, 8),
        "PHY-MILLIKAN": (8, 8),
        "PHY-E-DIFFRACTION": (4, 4),
        "PHY-HV-SUPPLY": (4, 4),
        "PHY-NMR": (4, 4),
        "PHY-MICROWAVE": (4, 4),
        "PHY-XRAY": (2, 2),
        "PHY-XRAY-SPECTROMETER": (2, 2),
        "PHY-OSCILLOSCOPE": (4, 4),
    },
    "D101": {
        "PHY-CALIPER": (15, 15),
        "PHY-MICROMETER": (15, 15),
        "PHY-BALANCE": (6, 6),
        "PHY-SURFACE": (8, 8),
    },
    "D102": {
        "PHY-SPECIFIC-HEAT": (8, 8),
        "PHY-BALANCE": (8, 8),
        "PHY-THERMOMETER": (8, 8),
        "PHY-ULTRASOUND": (8, 8),
        "PHY-OSCILLOSCOPE": (8, 8),
        "PHY-THERMOCOUPLE": (8, 8),
        "PHY-MULTIMETER": (8, 8),
    },
}


def build_reserved_inventory_specs(
    group_sizes: dict[str, int] | None = None,
) -> dict[str, dict[str, tuple[int, int]]]:
    """按核定容量和两组备用计算演示实验室的最低库存。"""

    sizes = group_sizes or {}
    result = {
        lab_code: dict(inventory)
        for lab_code, inventory in LAB_INVENTORY_SPECS.items()
    }
    for project_code, project in PROJECT_RESOURCE_SPECS.items():
        group_size = sizes.get(project_code, 1)
        for lab_code in project.lab_codes:
            capacity = PROJECT_LAB_CAPACITIES[project_code][lab_code]
            groups = ceil(capacity / group_size)
            inventory = result[lab_code]
            for item in project.requirements:
                if not item.required:
                    continue
                minimum = (groups + RESERVE_GROUPS) * item.units_per_group
                current_total, current_usable = inventory.get(
                    item.equipment_code, (0, 0)
                )
                usable = max(current_usable, minimum)
                total = max(current_total, usable)
                inventory[item.equipment_code] = (total, usable)
    return result


def validate_catalog() -> list[str]:
    """返回数据目录中的结构错误，空列表表示目录自洽。"""

    errors: list[str] = []
    reserved_inventory = build_reserved_inventory_specs()
    for project_code, project in PROJECT_RESOURCE_SPECS.items():
        if not project.requirements:
            errors.append(f"{project_code} 未配置器材需求")
        for item in project.requirements:
            if item.equipment_code not in EQUIPMENT_BY_CODE:
                errors.append(
                    f"{project_code} 引用了未知器材 {item.equipment_code}"
                )
            if item.units_per_group < 1:
                errors.append(
                    f"{project_code} 的 {item.equipment_code} 每组数量无效"
                )
        for lab_code in project.lab_codes:
            inventory = reserved_inventory.get(lab_code)
            if inventory is None:
                errors.append(f"{project_code} 引用了未知实验室 {lab_code}")
                continue
            for item in project.requirements:
                usable = inventory.get(item.equipment_code, (0, 0))[1]
                if item.required and usable < item.units_per_group:
                    errors.append(
                        f"{project_code} 在 {lab_code} 缺少 "
                        f"{item.equipment_code}"
                    )
            if (
                PROJECT_LAB_CAPACITIES.get(project_code, {}).get(lab_code)
                is None
            ):
                errors.append(
                    f"{project_code} 在 {lab_code} 缺少核定容量"
                )
    return errors
