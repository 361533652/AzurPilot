"""舰队科技的实例隔离、事务编辑与心智清单同步接口。"""
from module.config.transaction import config_transaction
from module.api.protocol import ApiError
from module.runtime import fleet_tech as tech
from module.runtime import dock_data
from module.runtime.fleet_database import FleetDatabase, FleetDatabaseConflict, FleetTechnologyConflict


class FleetTechService:
    def __init__(self, configs):
        self.configs = configs

    def catalog(self, instance):
        self.configs.path(instance)
        return tech.public_catalog()

    def report(self, instance):
        data = FleetDatabase(self.configs.path(instance)).load(include_import_status=True)
        snapshot = dock_data.read_snapshot(data)
        fields = self.configs.read(instance)[0].get('DockScan', {}).get('Dock', {})
        min_level = snapshot['min_level'] if dock_data.has_snapshot(data) else fields.get('MinLevel', 1)
        max_level = snapshot['max_level'] if dock_data.has_snapshot(data) else fields.get('MaxLevel', 125)
        record = dock_data.technology_record(data)
        return dict(instance=instance, min_level=min_level, max_level=max_level,
                    legacy_imported=data['legacy_imported'],
                    **tech.report(record))

    def patch(self, instance, expected_revision, changes, nations):
        path = self.configs.path(instance)
        with self.configs.lock, config_transaction(path):
            self.configs.path(instance)
            try:
                FleetDatabase(path).patch_technology(
                    [change.model_dump(exclude_unset=True) for change in changes], nations, expected_revision)
            except FleetTechnologyConflict as exc:
                raise ApiError('CONFLICT', str(exc), dict(conflicts=exc.conflicts)) from exc
            except FleetDatabaseConflict as exc:
                raise ApiError('CONFLICT', str(exc)) from exc
            except (ValueError, TypeError, KeyError) as exc:
                raise ApiError('INVALID_PARAMS', str(exc)) from exc
        return self.report(instance)

    def sync_mind(self, instance):
        """保留旧接口为只读报告；两个分析已经共用数据库，不再复制数据。"""
        return self.report(instance)
