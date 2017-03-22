# -*- coding: utf-8 -*-
from odoo import http,SUPERUSER_ID
import odoo
from datetime import datetime, timedelta
from odoo.http import request
from odoo.addons.web.controllers.main import DataSet
from common import image_url, utcStrTime, utctime_start_thisday, localizeStrTime, cn_strftime_zeropoint, utctime_start_lastdays, cn_current_year_and_date
from common import cn_yesterday_year_and_date
from common import utc_strftime_zeropoint
import odoo.tools.config as config
from odoo.tools import float_compare
import logging
import json
import time
from common import utcStrTime,utc_strftime_zeropoint,utctime_start_lastdays,utc_strftime_zeropoint
import requests
from odoo.addons.hollywant_base.tools.version_utils import versionCompare


_logger = logging.getLogger(__name__)


def predispatch(func):  # 分发前操作，目前主要是登录
    def _predispatch(*args, **kwargs):
        _logger.debug("Begin predispatch ...")
        if not request.session.uid:  # 如果session过期或者不存在，尝试登陆
            db = config.get('db_name')   # 直接读取odoo config 文件
            if not db:
                db='svm'
            login = config.get('admin_user', "admin")
            password = config.get('admin_pass',"admin@1234")
            _logger.error("public_user:" +login)
            dbs = odoo.service.db.list_dbs(False)
            if db not in dbs:
                return {
                    "title": "用户初始化",
                    "error": "数据库不存在！"
                }
            request.session.authenticate(db, login, password)

        if not request.session.uid:
            return {
                "title": "用户初始化",
                "error": "用户名或密码错误，请重新登录！"
            }
        request.uid = request.session.uid
        if request.uid:
            request.env.uid = request.uid
        _logger.debug("End predispatch ...\n")
        return func(*args, **kwargs)
    return _predispatch


def get_distance(lon1, lat1, lon2, lat2):  # 经度1，纬度1，经度2，纬度2 （十进制度数）hyf
    from math import radians, cos, sin, asin, sqrt
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees)
    """
    # 将十进制度数转化为弧度
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    # 公式
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2.0)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2.0)**2
    c = 2 * asin(sqrt(a))
    r = 6371  # 地球平均半径，单位为公里
    return c * r * 1000

class vmcController(DataSet):
    

   
    @predispatch  # 分发前检查登录，如没有登录，使用配置账户登录
    def vmc_machine_init(self, *args, **kwargs):  # 售货机初始化
        _logger.debug("Begin vmc_machine_init ...")
        if 'factory_code' in kwargs:
            factory_code = kwargs['factory_code']
            request.cr.execute("""
                select id,stack_qty,payment_alipay_account,payment_weixin_account,payment_wangbi_account from vmc_machines where factory_code =%s
            """, (factory_code,))
            machine_result = request.cr.dictfetchall()
            record = {}
            if len(machine_result) != 0:

                record['machine_id'] = machine_result[0]['id']
                record['stack_qty'] = machine_result[0]['stack_qty']
                record['payment_alipay_account'] = machine_result[0]['payment_alipay_account'] or ""
                record['payment_weixin_account'] = machine_result[0]['payment_weixin_account'] or ""
                record['payment_wangbi_account'] = machine_result[0]['payment_wangbi_account'] or ""
        _logger.debug("End vmc_machine_init ...\n")
        return record

    
    # 提货码接口
    @predispatch
    def goods_picked(self, *args, **kwargs):
        if 'pickgoods_code' and 'machine_id' in kwargs:
            if 'is_goods_shipped' not in kwargs:
                is_goods_shipped = 2  # 兼容老代码对应的售卖app代码的逻辑
            else:
                is_goods_shipped = kwargs['is_goods_shipped']
            pickgoods_code = kwargs['pickgoods_code']
            current_time = datetime.utcnow()
            current_time_str = datetime.strftime(current_time, "%Y-%m-%d %H:%M:%S")
            # 商品出货成功后会带着参数is_goods_shipped = 1再次调用提货码接口来让提货码失效
            if is_goods_shipped in (0, 2):
                machine_id = kwargs['machine_id']
                request.cr.execute(
                    """
                        select vp.id as code_id , pt.name, pt.net_weight, pu.name as unit, pp.id
                        from vmc_pickgoods_code as vp, product_product as pp, product_template as pt, product_uom as pu
                        where vp.pickgoods_code = %s and vp.is_valid = True and vp.product_id = pp.id
                        and pp.product_tmpl_id = pt.id and pt.uom_id = pu.id and vp.start_time < %s and vp.end_time > %s
                    """,
                    (pickgoods_code, current_time_str, current_time_str)
                )
                r = request.cr.dictfetchone()
                if not r:
                    return {'msg': '提货码无效', "code": 200}

                code_id = r['code_id']
                request.cr.execute(
                    """
                        select vc.machines_id
                        from vmc_code_machines_relation as vc
                        where vc.pickgoods_code = %s and vc.machines_id = %s
                        """, (code_id, machine_id,)
                )
                result = request.cr.dictfetchone()
                if result is not None:
                    product = request.env['product.product'].sudo().browse(
                        r['id'])
                    host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                    host_url_replaced = host_url_replaced.replace(':88', '')
                    r['image_url'] = host_url_replaced + image_url(
                        product.product_tmpl_id, 'image', '300x300')
                    # 料道号
                    request.cr.execute(
                        """select stack_number as stack_no, box_number as box_no
                           from vmc_machine_stacks
                           where product_id = %s and vmc_machine_id = %s
                        """, (r['id'], machine_id))
                    s = request.cr.dictfetchone()
                    if s is not None:
                        r['stack_no'] = s['stack_no']
                        r['box_no'] = s['box_no'] or ""
                        r['product_quantity'] = 1
                        if is_goods_shipped == 2:  # 兼容老代码对应的售卖app代码的逻辑
                            request.cr.execute(
                                """update vmc_pickgoods_code
                                   set is_valid = False
                                   where pickgoods_code = %s
                                """, (pickgoods_code,)
                            )
                        return r
                    else:
                        return {'msg': '找不到商品', "code": 200}
                else:
                    return {'msg': '提货码无效', "code": 200}
            else:  # 商品出货成功
                request.cr.execute(
                    """update vmc_pickgoods_code
                       set is_valid = False
                       where pickgoods_code = %s
                    """, (pickgoods_code,)
                )
                return {'msg': '出货成功, 提货码已失效', "code": 200}

        else:
            return {'msg': '上传参数有误', "code": 200}

   

   

    # 实时库存上报接口
    @predispatch
    def vmc_stock_sync(self, *args, **kwargs):
        _logger.debug("Begin vmc_stock_sync ...")
        # result = {"msg": "success"}
        # return result
        self._update_stock_status(request, **kwargs)
        current_time = datetime.utcnow()
        create_time = kwargs.get('create_time', False)
        if create_time:
            current_time = datetime.strptime(
                create_time, '%Y/%m/%d %H:%M:%S') - timedelta(hours=8)
        error = {}
        stocks = kwargs['stocks']
        machine_id = int(kwargs['machine_id'])
        if 'machine' in kwargs:
            if 'location' in kwargs['machine']:
                location = kwargs['machine']['location']  # 获取售货机位置，经纬度 hyf
            else:
                error['message'] = 'location不存在'
                return error
        else:
            error['message'] = 'machine'
            return error
        request.cr.execute(
            '''
                select coordinate
                from vmc_machines
                where id=%s
            ''' % (str(machine_id)))
        coordinate_dict = request.cr.dictfetchall()
        if coordinate_dict:
            coordinate_list = coordinate_dict[0]['coordinate'].split(',')
            lon1 = float(coordinate_list[0])  # 原经度
            lat1 = float(coordinate_list[1])  # 原纬度
            if not location:
                # 地理位置异常字段: 无法获取地理位置信息
                is_location_abnormal = '2'
            else:
                location_list = location.split(',')
                lon2 = float(location_list[0])  # 现经度
                lat2 = float(location_list[1])  # 现纬度
                request.cr.execute(
                    '''
                        select check_distance
                        from vmc_settings
                        order by id
                        desc limit 1
                    ''')
                check_distance_dict = request.cr.dictfetchall()
                if check_distance_dict:
                    check_distance = check_distance_dict[0]['check_distance']
                else:
                    check_distance = 100
                miss_distance = get_distance(lon1, lat1, lon2, lat2)
                distance = float_compare(miss_distance, check_distance, 4)
                if distance > 0:  # 距离差大于警戒距离
                    # 地理位置异常字段
                    is_location_abnormal = '1'
                else:
                    is_location_abnormal = '0'

                    request.cr.execute(
                        '''
                        select id
                        from vmc_machine_monitoring
                        where vmc_machine_id = %s
                        ''', (machine_id,))
                    monitoring_record = request.cr.dictfetchall()
                    if monitoring_record:
                        request.cr.execute(
                            '''
                            update vmc_machine_monitoring
                            set is_location_abnormal = %s
                            where vmc_machine_id = %s
                            ''', (is_location_abnormal, machine_id))
        if kwargs['machine']['status']:
            machine_status_info = kwargs['machine']['status']
            try:
                machine_status = json.loads(machine_status_info)
            except Exception, e:
                result = {"msg": "售货机监控信息格式有误"}
                return result
            # 字段信息:找零不足
            is_leakchange_5jiao = str(machine_status.get('isLackOf50Cent', 'false')).lower()
            is_leakchange_1yuan = str(machine_status.get('isLackOf100Cent', 'false')).lower()
            # 料道售空
            is_load_soldout = str(machine_status.get('isSoldOut', 'false')).lower()
            # 门异常
            is_dooropen = str(machine_status.get('isDoorOpened', 'false')).lower()
            # 串口通讯异常
            is_vmc_disconnected = str(machine_status.get('isVMCDisconnected', 'false')).lower()
            request.cr.execute(
                '''
                select id
                from vmc_machine_monitoring
                where vmc_machine_id = %s
                ''', (machine_id,))
            monitoring_record = request.cr.dictfetchall()
            if monitoring_record:
                request.cr.execute(
                    '''
                    update vmc_machine_monitoring
                    set is_location_abnormal = %s,
                        is_leakchange_5jiao = %s,
                        is_leakchange_1yuan = %s,
                        is_load_soldout = %s,
                        is_dooropen = %s,
                        is_vmc_disconnected = %s,
                        monitoring_time = %s
                    where vmc_machine_id = %s
                    ''',
                    (is_location_abnormal, is_leakchange_5jiao, is_leakchange_1yuan, is_load_soldout, is_dooropen,
                     is_vmc_disconnected,
                     current_time, machine_id))
            else:
                request.cr.execute(
                    '''
                    insert into vmc_machine_monitoring(is_location_abnormal,
                       is_leakchange_5jiao,
                       is_leakchange_1yuan,
                       is_load_soldout,
                       is_dooropen,
                       is_vmc_disconnected,
                       monitoring_time,
                       vmc_machine_id)
                    values(%s,%s,%s,%s,%s,%s,%s,%s)
                    ''',
                    (is_location_abnormal, is_leakchange_5jiao, is_leakchange_1yuan, is_load_soldout, is_dooropen,
                     is_vmc_disconnected, current_time, machine_id))
        # 判断6个监控数据是否有某个异常,有的话更新vmc_machines表相应字段
        info = (is_location_abnormal, is_leakchange_1yuan, is_load_soldout, is_dooropen, is_vmc_disconnected,
                is_leakchange_5jiao)
        if 'true' in info or '1' in info or '2' in info:
            error_info = '异常'
            error_info_color = 'red'
        else:
            error_info = '正常'
            error_info_color = 'green'
        # 有调用接口就更新网络状态为网络正常
        net_status = "网络正常"
        request.cr.execute(
            '''
            update vmc_machines
            set error_info = %s,
                error_info_color = %s,
                last_update_time = %s,
                net_status = %s
            where id = %s
            ''', (error_info, error_info_color, current_time, net_status, machine_id))
        

    # 获取支付url接口 （svm<--->odoo<--->网关）  by dazhao
    @predispatch
    def vmc_payment(self, *args, **kwargs):
        _logger.debug("Begin  vmc_payment ...")
        order_id = kwargs['order_id'] if 'order_id' in kwargs else ''  # 订单号
        total_amount = kwargs['total_amount'] if 'total_amount' in kwargs else ''  # 金额数
        payment_type = kwargs['payment_type'] if 'payment_type' in kwargs else ''  # 支付类型
        machine_id = kwargs['machine_id'] if 'machine_id' in kwargs else ''  # 机器id
        body = kwargs['body'] if 'body' in kwargs else ''  # 商品名称
        request.cr.execute(
            """
          SELECT C.name,C.payment_id,C.payment_secret
          FROM vmc_machines M,res_company C
          WHERE M.id=%s AND M.company_id=C.id
            """, (machine_id,))
        payment_id_secret_dict = request.cr.dictfetchone()
        if payment_id_secret_dict is None:
            return {'error': 'machine_id not exist'}
        if not payment_id_secret_dict['payment_id'] or not payment_id_secret_dict['payment_secret']:
            # 如果查询结果为空，这里要去取默认的一个值
            payment_id_secret_dict['payment_id'] = config["hollywant_payment_id"]
            payment_id_secret_dict['payment_secret'] = config["hollywant_secret_key"]

        payment_gateway_url = config.get("payment_gateway_url", False)

        if not payment_gateway_url:
            return {
                'error': "Payment Gateway isn't confirgured"
            }

        payment_message_dict = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "call",
            "params": {
                "source": 'svm',
                "method": "pay_qrcode",
                "hollywant_payment_id": payment_id_secret_dict['payment_id'],
                "hollywant_secret_key": payment_id_secret_dict['payment_secret'],
                "payment_type": payment_type,
                "payment_order": {
                    "order_id": order_id,
                    "total_amount": total_amount,
                    "body":body
                },
            }
        }

        payment_gateway_url = config.get("payment_gateway_url", False)

        if not payment_gateway_url:
            return {
                'error': "Payment Gateway isn't confirgured"
            }

        ret = requests.post(url=payment_gateway_url,
                            data=json.dumps(payment_message_dict), headers={'Content-Type': 'application/json'})
        if 'error' in ret.json():
            return ret.json()['error']
        _logger.debug("End vmc_payment ...")
        return ret.json()['result']

    # 订单查询接口 （svm<--->odoo<--->网关）  by dazhao
    @predispatch
    def vmc_query_order(self, *args, **kwargs):
        _logger.debug("Begin  vmc_query_order ...")
        order_id = kwargs['order_id'] if 'order_id' in kwargs else ''  # 订单号
        machine_id = kwargs['machine_id'] if 'machine_id' in kwargs else ''  # 机器id
        request.cr.execute(
            """
          SELECT C.name,C.payment_id,C.payment_secret
          FROM vmc_machines M,res_company C
          WHERE M.id=%s AND M.company_id=C.id
            """, (machine_id,))
        payment_id_secret_dict = request.cr.dictfetchone()

        if payment_id_secret_dict is None:
            return {'error': 'machine_id not exist'}
        if not payment_id_secret_dict['payment_id'] or not payment_id_secret_dict['payment_secret']:
            # 如果查询结果为空，这里要去取默认的一个值
            payment_id_secret_dict['payment_id'] = config["hollywant_payment_id"]
            payment_id_secret_dict['payment_secret'] = config["hollywant_secret_key"]
        query_order_dict = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "call",
            "params": {
                "method": "query_order",
                "order_id": order_id,
                "hollywant_payment_id": payment_id_secret_dict['payment_id'],
                "hollywant_secret_key": payment_id_secret_dict['payment_secret'],
                "source": 'svm',
            }
        }

        payment_gateway_url = config.get("payment_gateway_url", False)

        if not payment_gateway_url:
            return {
                'error': "Payment Gateway isn't confirgured"
            }

        ret = requests.post(url=payment_gateway_url,
                            data=json.dumps(query_order_dict), headers={'Content-Type': 'application/json'})
        if 'error' in ret.json():
            return ret.json()
        result_dict = ret.json()['result']
        if 'error' in result_dict:
            result_dict['order_status'] = -1
            return result_dict  # 发生错误统一，返回-1

        if 'msg' in result_dict:
            if result_dict['msg'] == 'WAIT_PAY' or result_dict['msg'] == 'NOT_PAY':
                result_dict['order_status'] = 0  # 等待付款，返回0
            elif result_dict['msg'] == 'SUCCESS':
                result_dict['order_status'] = 1  # 付款成功，返回1
            else:
                result_dict['order_status'] = 2  # 其他情况，返回2

        _logger.debug("End vmc_query_order ...")
        return result_dict

   
