"""把岛屿生产规划结果换算成 AzurPilot 已有的岛屿配置。

规划算法来自 AzurLaneAutoScript（见 calculator.py），本文件是 AzurPilot 专属的适配层，
不依赖设备，便于离线测试：

- 店铺后厨 `Island{Restaurant,Teahouse,JuuEatery,Grill,JuuCoffee}.Meal1~8/MealNumber1~8`：
  先列出售菜品（按日销量降序），再列原料/中间品（按日消耗降序）。出售菜品的目标库存为
  一个货架容量 + 一天销量，与 ALAS「餐厅保留 + 日缓冲」一致；中间品目标为一天消耗量。
- 挂机生产 `Island*NextTask.AwayCook`：该后厨闲时累积量最大的产品，没有则为 None。
- 商区 `IslandBusinessShop1~5.Product1~5`：该餐厅计划出售的菜品，按日销量降序。
- 农田 `IslandFarm/IslandOrchard/IslandNursery.CropTargets`：区域内所有作物都写明目标，
  计划消耗的作物为一天消耗量（再加一天闲时累积量），不需要的作物为 0。

AzurPilot 不会做或卖的菜品、不会种的作物，会在求解前从模型中排除，
避免规划出 AzurPilot 执行不了的方案。
"""
from math import ceil

from module.island.farm_targets import format_crop_targets
from module.island_planner.data import DIC_ISLAND_ITEM, DIC_ISLAND_RECIPE
from module.island_planner.restaurant_config import (
    WAITRESS_ANY,
    WAITRESS_NONE,
    get_restaurant_config,
)

GRADES = ('bronze', 'silver', 'gold', 'diamond')
MEAL_SLOTS = 8
PRODUCT_SLOTS = 5

# 餐厅编号 -> AzurPilot 配置组。slot_group 为 calculator.SLOT_TO_GROUP 中的后厨分组。
KITCHENS = {
    601: {'name': 'Restaurant', 'task': 'IslandRestaurant', 'business': 'IslandBusinessShop1', 'slot_group': 'koi'},
    602: {'name': 'Teahouse', 'task': 'IslandTeahouse', 'business': 'IslandBusinessShop2', 'slot_group': 'bear'},
    603: {'name': 'JuuEatery', 'task': 'IslandJuuEatery', 'business': 'IslandBusinessShop3', 'slot_group': 'eatery'},
    604: {'name': 'Grill', 'task': 'IslandGrill', 'business': 'IslandBusinessShop4', 'slot_group': 'grill'},
    901: {'name': 'JuuCoffee', 'task': 'IslandJuuCoffee', 'business': 'IslandBusinessShop5', 'slot_group': 'cafe'},
}
BUSINESS_TASK = 'IslandBusiness'

# AzurPilot 菜品名 -> 游戏物品 ID（按中文名与 data.py 对照得到）
DISH_ITEM_IDS = {
    # 有鱼餐馆
    'tofu': 3011, 'tofu_meat': 3012, 'omurice': 3013, 'cabbage_tofu': 3014, 'salad': 3015,
    'tofu_combo': 3101, 'hearty_meal': 3102, 'fish_chip': 3114, 'onion_fish': 3116, 'fo_tiao': 3120,
    'persimmon_cake': 4009, 'matsutake_chicken_soup': 4010, 'double_bamboo_shoots': 4025,
    'asparagus_shrimp': 4026, 'amaranth_rice_ball': 4039,
    # 白熊饮品
    'apple_juice': 3017, 'banana_mango': 3018, 'honey_lemon': 3019, 'strawberry_lemon': 3020,
    'lavender_tea': 3021, 'strawberry_honey': 3022, 'floral_fruity': 3103, 'fruit_paradise': 3104,
    'sunny_honey': 3105, 'carrot_pear_juice': 4013, 'chrysanthemum_tea': 4014,
    'spring_flower_tea': 4024, 'watermelon_juice': 4038,
    # 啾啾简餐
    'apple_pie': 3009, 'corn_cup': 3023, 'orange_pie': 3024, 'rice_mango': 3025, 'banana_crepe': 3026,
    'strawberry_charlotte': 3028, 'succulently_sweet': 3106, 'orchard_duo': 3107, 'berry_orange': 3108,
    'seafood_rice': 3118,
    # 烧烤店
    'roasted_skewer': 3029, 'chicken_potato': 3030, 'stir_fried_chicken': 3032, 'carrot_omelette': 3033,
    'steak_bowl': 3034, 'carnival': 3109, 'double_energy': 3110, 'crayfish_stir_fry': 3119,
    # 啾咖啡
    'iced_coffee': 3005, 'cheese': 3006, 'latte': 3007, 'citrus_coffee': 3008,
    'strawberry_milkshake': 3010, 'omelette': 3059, 'morning_light': 3111, 'wake_up_call': 3112,
    'fruity_fruitier': 3113,
}

# AzurPilot 作物名 -> 游戏物品 ID，按 IslandFarm 的三个区域分组
CROP_ITEM_IDS = {
    'farm': {
        'wheat': 2000, 'corn': 2001, 'rice': 2002, 'chinese_cabbage': 2003, 'potato': 2005,
        'soybean': 2006, 'pasture': 2008, 'coffee_bean': 2009,
    },
    'orchard': {
        'apple': 2016, 'citrus': 2017, 'banana': 2018, 'mango': 2019, 'lemon': 2020,
        'avocado': 2021, 'rubber': 2022, 'pear': 4005, 'persimmon': 4007,
    },
    'nursery': {
        'carrot': 2004, 'onion': 2007, 'flax': 2010, 'strawberry': 2011, 'cotton': 2012,
        'tea': 2014, 'lavender': 2015, 'pineapple': 4021, 'asparagus': 4019,
    },
}
CROP_AREAS = {
    'farm': {'task': 'IslandFarm', 'group': 'IslandFarm', 'slot_group': 'field'},
    'orchard': {'task': 'IslandFarm', 'group': 'IslandOrchard', 'slot_group': 'orchard'},
    'nursery': {'task': 'IslandFarm', 'group': 'IslandNursery', 'slot_group': 'nursery'},
}

# AzurPilot 商区角色名 -> ALAS 服务员名（只有这些角色有容量或售价加成）
WAITRESS_ALIASES = {
    'ChaoHo': 'Chao_Ho',
    'Cheshire': 'Cheshire',
    'Helena': 'Helena',
    'Eugen': 'Prinz_Eugen',
    'August': 'August_von_Parseval',
    'Belfast': 'Belfast',
    'ChangFeng': 'Chang_Feng',
}


def recipe_product(recipe_id):
    return next(iter(DIC_ISLAND_RECIPE[recipe_id]['commission_product']))


def waitress_slots(restaurant_id, chars):
    """把商区 Char1/Char2 换算成 ALAS 的服务员槽位。

    AzurPilot 中 None 表示由工作啾代班，店照常营业，因此与其他无加成角色一样视为 any；
    对本店有加成的角色保留名字。同一加成角色重复填写时第二个视为 any，避免 ALAS 判为非法配置。
    """
    options = get_restaurant_config(restaurant_id)['waitress_options']
    slots = []
    for char in chars:
        name = WAITRESS_ALIASES.get(char)
        if name in options and name not in slots:
            slots.append(name)
        else:
            slots.append(WAITRESS_ANY)
    return tuple(slots)


def open_restaurants(batch_enabled, batch1, batch2):
    """商区会营业的餐厅编号。分批经营开启时只有列在某一批里的店营业。"""
    if not batch_enabled:
        return set(KITCHENS)
    indexes = {int(index) for index in list(batch1 or []) + list(batch2 or [])}
    return {rid for rid, kitchen in KITCHENS.items() if int(kitchen['business'][-1]) in indexes}


def restaurant_settings(grades, chars, open_ids=None):
    """
    Args:
        grades (dict): {餐厅编号: 'bronze'|'silver'|'gold'|'diamond'}。
        chars (dict): {餐厅编号: (Char1, Char2)}，来自 IslandBusinessShopN。
        open_ids (set): 营业的餐厅编号，None 为全部营业。不营业的店不参与出售。

    Returns:
        dict: calculator 的 restaurant_settings 参数。
    """
    settings = {}
    for restaurant_id in KITCHENS:
        grade = grades.get(restaurant_id) or 'bronze'
        if grade not in GRADES:
            raise ValueError(f'Invalid restaurant grade: {grade}')
        if open_ids is not None and restaurant_id not in open_ids:
            slots = (WAITRESS_NONE, WAITRESS_NONE)
        else:
            slots = waitress_slots(restaurant_id, chars.get(restaurant_id, (None, None)))
        settings[restaurant_id] = {'grade': grade, 'waitress_slots': slots}
    return settings


def restrict_calculator(calculator, meal_options, product_options):
    """求解前排除 AzurPilot 执行不了的配方与菜品。

    Args:
        calculator (ProductionPlanCalculator): 已按科技初始化，尚未求解。
        meal_options (dict): {餐厅编号: AzurPilot 后厨可选菜品名}。
        product_options (dict): {餐厅编号: AzurPilot 商区可选菜品名}。

    Returns:
        list[str]: 被排除的配方说明，供日志查看。
    """
    allowed_products = {}
    for restaurant_id, kitchen in KITCHENS.items():
        allowed_products[kitchen['slot_group']] = {
            DISH_ITEM_IDS[name] for name in meal_options.get(restaurant_id, ()) if name in DISH_ITEM_IDS}
    for area, data in CROP_AREAS.items():
        allowed_products[data['slot_group']] = set(CROP_ITEM_IDS[area].values())

    excluded = []
    for recipe_id, group in calculator.recipe_group.items():
        if group not in allowed_products or not calculator.recipe_available.get(recipe_id, False):
            continue
        if recipe_product(recipe_id) not in allowed_products[group]:
            calculator.recipe_available[recipe_id] = False
            excluded.append(DIC_ISLAND_RECIPE[recipe_id]['name']['en'])

    calculator.sellable_items = {
        restaurant_id: {DISH_ITEM_IDS[name] for name in product_options.get(restaurant_id, ())
                        if name in DISH_ITEM_IDS}
        for restaurant_id in KITCHENS
    }
    return excluded


def _item_name_map(names):
    return {DISH_ITEM_IDS[name]: name for name in names if name in DISH_ITEM_IDS}


def _produced_items(calculator, slot_group):
    """该分组计划生产的产品 -> 每日产量。"""
    produced = {}
    for recipe_id, batches in calculator.production_plan.items():
        if calculator.recipe_group.get(recipe_id) != slot_group or batches <= 1e-6:
            continue
        for item_id, amount in DIC_ISLAND_RECIPE[recipe_id]['commission_product'].items():
            produced[item_id] = produced.get(item_id, 0) + amount * batches
    return produced


def kitchen_settings(calculator, restaurant_id, meal_options, product_options):
    """单个店铺的后厨、挂机与商区配置。

    Returns:
        tuple[dict, list[str]]: ({配置路径: 值}, 提示信息)。
    """
    kitchen = KITCHENS[restaurant_id]
    task = kitchen['task']
    notes = []
    meal_names = _item_name_map(meal_options)
    product_names = _item_name_map(product_options)
    capacity = calculator.restaurant_capacity[restaurant_id]
    buffer = calculator.product_daily_buffer_items
    idle = calculator.idle_accumulating_items_per_day

    sales = {
        item_id: amount for (slot, item_id), amount in calculator.sell_plan.items()
        if slot == restaurant_id and amount > 1e-6
    }
    produced = _produced_items(calculator, kitchen['slot_group'])

    meals = []
    for item_id in sorted(sales, key=lambda i: (-sales[i], i)):
        if item_id in meal_names:
            meals.append((meal_names[item_id], capacity + ceil(buffer.get(item_id, sales[item_id]) - 1e-9)))
    sold = {item_id for item_id in sales}
    others = [item_id for item_id in produced if item_id not in sold and buffer.get(item_id, 0) > 0]
    for item_id in sorted(others, key=lambda i: (-buffer[i], i)):
        if item_id in meal_names:
            meals.append((meal_names[item_id], int(buffer[item_id])))
    if len(meals) > MEAL_SLOTS:
        dropped = [name for name, _ in meals[MEAL_SLOTS:]]
        notes.append(f'{task}: only {MEAL_SLOTS} meal slots, dropped {dropped}')
        meals = meals[:MEAL_SLOTS]

    values = {}
    for index in range(MEAL_SLOTS):
        name, number = meals[index] if index < len(meals) else ('None', 0)
        values[f'{task}.{task}.Meal{index + 1}'] = name
        values[f'{task}.{task}.MealNumber{index + 1}'] = number

    idle_items = [item_id for item_id in produced if idle.get(item_id, 0) > 0 and item_id in meal_names]
    away = max(idle_items, key=lambda i: (idle[i], -i)) if idle_items else None
    values[f'{task}.{task}NextTask.AwayCook'] = meal_names[away] if away else 'None'

    products = [product_names[item_id] for item_id in sorted(sales, key=lambda i: (-sales[i], i))
                if item_id in product_names]
    if len(products) > PRODUCT_SLOTS:
        notes.append(f'{kitchen["business"]}: only {PRODUCT_SLOTS} product slots, dropped {products[PRODUCT_SLOTS:]}')
        products = products[:PRODUCT_SLOTS]
    business = kitchen['business']
    for index in range(PRODUCT_SLOTS):
        values[f'{BUSINESS_TASK}.{business}.Product{index + 1}'] = \
            products[index] if index < len(products) else 'None'
    return values, notes


def crop_settings(calculator):
    """三个种植区域的 CropTargets。"""
    buffer = calculator.product_daily_buffer_items
    idle = calculator.idle_accumulating_items_per_day
    values = {}
    for area, data in CROP_AREAS.items():
        targets = {}
        for crop, item_id in CROP_ITEM_IDS[area].items():
            amount = buffer.get(item_id, 0) + idle.get(item_id, 0)
            targets[crop] = ceil(amount - 1e-9) if amount > 0 else 0
        values[f'{data["task"]}.{data["group"]}.CropTargets'] = format_crop_targets(targets)
    return values


def build_settings(calculator, meal_options, product_options):
    """求解成功后，生成要写入的全部配置。

    Returns:
        tuple[dict, list[str]]: ({配置路径: 值}, 提示信息)。
    """
    values = {}
    notes = []
    for restaurant_id in KITCHENS:
        kitchen_values, kitchen_notes = kitchen_settings(
            calculator, restaurant_id,
            meal_options.get(restaurant_id, ()), product_options.get(restaurant_id, ()))
        values.update(kitchen_values)
        notes.extend(kitchen_notes)
    values.update(crop_settings(calculator))
    return values, notes


def item_label(item_id):
    return DIC_ISLAND_ITEM[item_id]['name']['en']
