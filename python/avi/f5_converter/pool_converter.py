import logging

import avi.f5_converter.conversion_util as conv_utils
import avi.f5_converter.converter_constants as conv_const

LOG = logging.getLogger(__name__)


class PoolConfigConv(object):
    @classmethod
    def get_instance(cls, version):
        if version == 10:
            return PoolConfigConvV10()
        if version == 11:
            return PoolConfigConvV11()
    supported_attr = None

    def convert_pool(self, pool_name, f5_config, avi_config):
        pass

    def convert(self, f5_config, avi_config):
        pool_list = []
        pool_config = f5_config['pool']
        for pool_name in pool_config.keys():
            LOG.debug("Converting Pool: %s" % pool_name)
            f5_pool = pool_config[pool_name]
            if not f5_pool:
                LOG.debug("Empty pool skipped for conversion :%s" % pool_name)
                conv_utils.add_status_row('pool', None, pool_name, 'skipped')
                continue
            try:
                pool_obj = self.convert_pool(pool_name, f5_config, avi_config)
                pool_list.append(pool_obj)
            except:
                LOG.error("Failed to convert pool: %s" % pool_name,
                          exc_info=True)
                conv_utils.add_status_row('pool', None, pool_name, 'Error')
            LOG.debug("Conversion successful for Pool: %s" % pool_name)
        avi_config['Pool'] = pool_list

    def get_monitor_refs(self, monitor_names, monitor_config_list, pool_name):
            skipped_monitors = []
            monitors = monitor_names.split(" ")
            monitor_refs = []
            garbage_val = ["and", "all", "min", "of", "{", "}", "none"]
            for monitor in monitors:
                if not monitor or monitor in garbage_val or \
                        monitor.isdigit():
                    continue
                monitor = monitor.strip()
                monitor_obj = [obj for obj in monitor_config_list
                               if obj["name"] == monitor]
                if monitor_obj:
                    monitor_refs.append(monitor_obj[0]["name"])
                else:
                    LOG.warning("Monitor not found: %s for pool %s" %
                                (monitor, pool_name))
                    skipped_monitors.append(monitor)
            return skipped_monitors, monitor_refs

    def create_pool_object(self, name, desc, servers, pd_action, algo,
                           ramp_time):
        pool_obj = \
            {
                "name": name,
                "description": desc,
                "servers": servers,
                "fail_action": pd_action,
                "lb_algorithm": algo
            }
        if ramp_time:
            pool_obj['connection_ramp_duration'] = ramp_time
        return pool_obj

    def add_status(self, name, skipped_attr, member_skipped_config,
                   skipped_monitors, pool_obj):
        skipped = []
        if skipped_attr:
            skipped.append(skipped_attr)
        if member_skipped_config:
            skipped.append(member_skipped_config)
        if skipped_monitors:
            skipped.append({"monitor": skipped_monitors})
        status = 'successful'
        if skipped:
            status = 'partial'
        conv_utils.add_status_row('pool', None, name, status, skipped,
                                  pool_obj)


class PoolConfigConvV11(PoolConfigConv):
    supported_attr = ['members', 'monitor', 'service-down-action',
                      'load-balancing-mode', 'description', 'slow-ramp-time']

    def convert_pool(self, pool_name, f5_config, avi_config):
        nodes = f5_config.get("node", {})
        f5_pool = f5_config['pool'][pool_name]
        monitor_config = avi_config['HealthMonitor']
        servers, member_skipped_config = self.convert_servers_config(
            f5_pool.get("members", {}), nodes)
        sd_action = f5_pool.get("service-down-action", "")
        pd_action = conv_utils.get_avi_pool_down_action(sd_action)
        lb_method = f5_pool.get("load-balancing-mode", None)
        lb_algorithm = self.get_avi_lb_algorithm(lb_method)
        desc = f5_pool.get('description', None)
        ramp_time = f5_pool.get('slow-ramp-time', None)
        pool_obj = super(PoolConfigConvV11, self).create_pool_object(
            pool_name, desc, servers, pd_action, lb_algorithm, ramp_time)
        monitor_names = f5_pool.get("monitor", None)
        skipped_monitors = []
        if monitor_names:
            skipped_monitors, monitor_refs = super(
                PoolConfigConvV11, self).get_monitor_refs(
                monitor_names, monitor_config, pool_name)
            pool_obj["health_monitor_refs"] = monitor_refs
        skipped_attr = [key for key in f5_pool.keys() if
                        key not in self.supported_attr]
        super(PoolConfigConvV11, self).add_status(
            pool_name, skipped_attr, member_skipped_config, skipped_monitors,
            pool_obj)
        return pool_obj

    def get_avi_lb_algorithm(self, f5_algorithm):
        """
        Converts f5 LB algorithm to equivalent avi LB algorithm
        :param f5_algorithm: f5 algorithm name
        :return: Avi LB algorithm enum value
        """
        avi_algorithm = None
        if not f5_algorithm or f5_algorithm in ["ratio-node", "ratio-member"]:
            avi_algorithm = "LB_ALGORITHM_ROUND_ROBIN"
        elif f5_algorithm in ["least-connections-member",
                              "least-connections-node", "least-sessions",
                              "weighted-least-connections-member",
                              "ratio-least-connections-member",
                              "ratio-least-connections-node",
                              "weighted-least-connections-node"]:
            avi_algorithm = "LB_ALGORITHM_LEAST_CONNECTIONS"
        elif f5_algorithm in ["fastest-node", "fastest-app-response"]:
            avi_algorithm = "LB_ALGORITHM_FASTEST_RESPONSE"
        elif f5_algorithm in ["dynamic-ratio-node", "observed-member",
                              "predictive-node", "dynamic-ratio-member",
                              "predictive-member", "observed-node"]:
            avi_algorithm = "LB_ALGORITHM_LEAST_LOAD"
        return avi_algorithm

    def convert_servers_config(self, servers_config, nodes):
        """
        Converts the config of servers in the pool
        :param servers_config: F5 servers config for particular pool
        :param nodes: F5 node config to resolve IP of the server
        :return: List of Avi server configs
        """
        server_list = []
        skipped_list = []
        supported_attributes = ['address', 'state', 'ratio']
        for server_name in servers_config.keys():
            server = servers_config[server_name]
            parts = server_name.split(':')
            ip_addr = nodes[parts[0]]["address"]
            port = parts[1] if len(parts) == 2 else conv_const.DEFAULT_PORT
            enabled = True
            state = server.get("state", 'enabled')
            if state == "user-down":
                enabled = False
            server_obj = {
                'ip': {
                    'addr': ip_addr,
                    'type': 'V4'
                },
                'port': port,
                'enabled': enabled
            }
            ratio = server.get("ratio", None)
            if ratio:
                server_obj["ratio"] = ratio
            server_list.append(server_obj)
            skipped = [key for key in server.keys()
                       if key not in supported_attributes]
            if skipped:
                skipped_list.append({server_name: skipped})
        return server_list, skipped_list


class PoolConfigConvV10(PoolConfigConv):
    supported_attr = ['members', 'monitor', 'action on svcdown', 'lb method',
                      'description', 'slow ramp time']

    def convert_pool(self, pool_name, f5_config, avi_config):
        nodes = f5_config.pop("node", {})
        f5_pool = f5_config['pool'][pool_name]
        monitor_config = avi_config['HealthMonitor']
        servers, member_skipped_config = self.convert_servers_config(
            f5_pool.get("members", {}), nodes)
        sd_action = f5_pool.get("action on svcdown", "")
        pd_action = conv_utils.get_avi_pool_down_action(sd_action)
        lb_method = f5_pool.get("lb method", None)
        lb_algorithm = self.get_avi_lb_algorithm(lb_method)
        desc = f5_pool.get('description', None)
        ramp_time = f5_pool.get('slow ramp time', None)
        pool_obj = super(PoolConfigConvV10, self).create_pool_object(
            pool_name, desc, servers, pd_action, lb_algorithm, ramp_time)
        monitor_names = f5_pool.get("monitor", None)
        skipped_monitors = []
        if monitor_names:
            skipped_monitors, monitor_refs = super(
                PoolConfigConvV10, self).get_monitor_refs(
                monitor_names, monitor_config, pool_name)
            pool_obj["health_monitor_refs"] = monitor_refs
        skipped_attr = [key for key in f5_pool.keys() if
                        key not in self.supported_attr]
        super(PoolConfigConvV10, self).add_status(
            pool_name, skipped_attr, member_skipped_config, skipped_monitors,
            pool_obj)
        return pool_obj

    def get_avi_lb_algorithm(self, f5_algorithm):
        """
        Converts f5 LB algorithm to equivalent avi LB algorithm
        :param f5_algorithm: f5 algorithm name
        :return: Avi LB algorithm enum value
        """
        avi_algorithm = None
        if not f5_algorithm or f5_algorithm in ["ratio", "member ratio"]:
            avi_algorithm = "LB_ALGORITHM_ROUND_ROBIN"
        elif f5_algorithm in ["member least conn", "least conn", "l3 addr",
                              "weighted least conn member", "least sessions",
                              "weighted least conn node addr"]:
            avi_algorithm = "LB_ALGORITHM_LEAST_CONNECTIONS"
        elif f5_algorithm in ["fastest", "fastest app resp"]:
            avi_algorithm = "LB_ALGORITHM_FASTEST_RESPONSE"
        elif f5_algorithm in ["dynamic ratio", "member observed", "predictive",
                              "member predictive", "observed",
                              "member dynamic ratio"]:
            avi_algorithm = "LB_ALGORITHM_LEAST_LOAD"
        return avi_algorithm

    def convert_servers_config(self, servers_config, nodes):
        """
        Converts the config of servers in the pool
        :param servers_config: F5 servers config for particular pool
        :return: List of Avi server configs
        """
        server_list = []
        skipped_list = []
        supported_attributes = ['session', 'ratio']
        for server_name in servers_config.keys():
            skipped = None
            server = servers_config[server_name]
            parts = server_name.split(':')
            ip_addr = parts[0]
            port = parts[1] if len(parts) == 2 else conv_const.DEFAULT_PORT
            if not port.isdigit():
                port = conv_utils.get_port_by_protocol(port)
            enabled = True
            state = 'enabled'
            ratio = None
            if server:
                state = server.get("session", 'enabled')
                skipped = [key for key in server.keys()
                           if key not in supported_attributes]
                ratio = server.get("ratio", None)
            if state == "user disabled":
                enabled = False
            server_obj = {
                'ip': {
                    'addr': ip_addr,
                    'type': 'V4'
                },
                'port': port,
                'enabled': enabled
            }
            if ratio:
                server_obj["ratio"] = ratio
            server_list.append(server_obj)
            if skipped:
                skipped_list.append({server_name: skipped})
        return server_list, skipped_list
