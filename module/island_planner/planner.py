"""岛屿生产规划器（工具页任务）。

思路与算法来自 AzurLaneAutoScript（LmeSzinc/AzurLaneAutoScript，GPL-3.0）的
IslandProductionPlanner：扫描岛屿科技，用线性规划求出每日生产/出售方案，
在每日岛币净收入不低于下限的前提下最大化季节 PT。AzurPilot 版只做「计算器」：
把方案写进 AzurPilot 已有的岛屿配置（店铺后厨、商区、农田作物目标），
实际生产仍由 AzurPilot 自己的岛屿任务执行。
"""
from module.base.decorator import cached_property
from module.config.deep import deep_get
from module.config.utils import filepath_args, read_file
from module.island_planner import settings_map
from module.island_planner.calculator import ProductionPlanCalculator
from module.island_planner.scanner import IslandTechnologyScanner
from module.island_planner.utils import (
    get_current_activity_list,
    get_current_season_remaining_days,
    load_technology_status,
)
from module.logger import logger

TASK = 'IslandProductionPlanner'
TECHNOLOGY_STORAGE = f'{TASK}.Storage.Storage.IslandTechnologyStatus'
GRADE_KEYS = {
    601: 'RestaurantGrade',
    602: 'TeahouseGrade',
    603: 'JuuEateryGrade',
    604: 'GrillGrade',
    901: 'JuuCoffeeGrade',
}


class IslandProductionPlanner(IslandTechnologyScanner):
    """
    Pages:
        in: 任意页面（需要扫描科技时）
        out: page_island_technology（扫描时），否则不操作游戏
    """

    def get(self, key):
        return getattr(self.config, f'{TASK}_{key}')

    @cached_property
    def args(self):
        return read_file(filepath_args())

    def options(self, task, group, key):
        """配置项的可选值（去掉 None），用于限定规划只用 AzurPilot 支持的菜品。"""
        return [name for name in deep_get(self.args, [task, group, key, 'option'], default=[]) if name != 'None']

    def load_technology(self):
        """读取已保存的科技状态；没有保存或要求重新扫描时扫描游戏。

        Returns:
            dict[int, bool]: {科技ID: 是否解锁}。
        """
        saved = self.config.cross_get(TECHNOLOGY_STORAGE, None)
        if saved and not self.get('RescanTechnology'):
            logger.info('[岛屿-规划] 使用已保存的岛屿科技状态')
            return load_technology_status(saved)

        logger.hr('Scan island technology', level=1)
        status = self.get_technology_status()
        unlocked = sum(1 for value in status.values() if value)
        logger.info(f'[岛屿-规划] 科技扫描完成：已解锁 {unlocked}/{len(status)}')
        with self.config.multi_set():
            # 键转成字符串保存，load_technology_status 读回时转成 int
            self.config.cross_set(TECHNOLOGY_STORAGE, {str(key): value for key, value in status.items()})
            self.config.cross_set(f'{TASK}.{TASK}.RescanTechnology', False)
        return status

    def create_calculator(self, technology_status):
        grades = {rid: self.get(key) for rid, key in GRADE_KEYS.items()}
        chars = {
            rid: (getattr(self.config, f'{kitchen["business"]}_Char1', None),
                  getattr(self.config, f'{kitchen["business"]}_Char2', None))
            for rid, kitchen in settings_map.KITCHENS.items()
        }
        open_ids = settings_map.open_restaurants(
            getattr(self.config, 'IslandBusiness_BatchEnabled', True),
            getattr(self.config, 'IslandBusiness_Batch1Shops', []),
            getattr(self.config, 'IslandBusiness_Batch2Shops', []),
        )
        return ProductionPlanCalculator(
            technology_status=technology_status,
            activity_list=get_current_activity_list(),
            place_efficiency={
                101: float(self.get('FieldsEfficiency')),
                501: float(self.get('OrchardEfficiency')),
                502: float(self.get('NurseryEfficiency')),
            },
            restaurant_settings=settings_map.restaurant_settings(grades, chars, open_ids),
            daily_profit_lower_limit=self.get('DailyProfitLowerLimit'),
            daily_buffer_safety_margin=self.get('DailyBufferSafetyMargin'),
        )

    def run(self):
        logger.hr('Island production planner', level=1)
        technology_status = self.load_technology()
        calculator = self.create_calculator(technology_status)

        meal_options = {
            rid: self.options(kitchen['task'], kitchen['task'], 'Meal1')
            for rid, kitchen in settings_map.KITCHENS.items()
        }
        product_options = {
            rid: self.options(settings_map.BUSINESS_TASK, kitchen['business'], 'Product1')
            for rid, kitchen in settings_map.KITCHENS.items()
        }
        excluded = settings_map.restrict_calculator(calculator, meal_options, product_options)
        if excluded:
            logger.info(f'[岛屿-规划] AzurPilot 不支持、已排除的配方: {", ".join(excluded)}')

        remaining = get_current_season_remaining_days()
        if remaining is not None:
            logger.info(f'[岛屿-规划] 本季剩余 {remaining:.1f} 天')
        calculator.solve_production_plan()
        calculator.print_solved_production_plan()
        if not calculator.lp_success:
            logger.warning('[岛屿-规划] 没有可行方案，配置未修改。可降低「每日岛币净收入下限」后重试')
            return False

        values, notes = settings_map.build_settings(calculator, meal_options, product_options)
        for note in notes:
            logger.warning(f'[岛屿-规划] {note}')
        changed = {path: value for path, value in values.items() if self.config.cross_get(path) != value}
        logger.hr('Planned settings', level=2)
        for path, value in values.items():
            mark = '*' if path in changed else ' '
            text = str(value).replace('\n', ', ')
            logger.info(f'{mark} {path} = {text}')

        if not self.get('ApplySettings'):
            logger.info(f'[岛屿-规划] 预览模式：{len(changed)} 项配置会改变，未写入。勾选「写入配置」后再次运行即可应用')
            return True
        with self.config.multi_set():
            self.config.cross_set_many(changed)
        logger.info(f'[岛屿-规划] 已写入 {len(changed)} 项配置')
        return True
