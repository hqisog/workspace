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
    @http.route(['/app_api/session/authenticate'], type='json', auth="none")
    def api_authenticate(self, *args, **kwargs):
        login = kwargs['login']
        password = kwargs['password']
        db = config["db_name"]
        request.session.authenticate(db, login, password)
        if self.session_info().get("uid") is False:
            return {
                "title": "用户初始化",
                "error": "用户名或密码错误，请重新登录！"
            }
        session_info = self.session_info()
        current_user = request.env['res.users'].browse(self.session_info().get("uid"))
        session_info['user_name'] = current_user.partner_id.name
        session_info['tags']=[str(request.session.uid),"p"+str(current_user.partner_id.id)]
        session_info['session_id']=request.session.uid
        request.cr.execute("""SELECT rg.name FROM res_groups rg LEFT JOIN ir_module_category imc ON imc.id = rg.category_id
                                                WHERE imc.name =%s AND rg.id IN  (
                                                SELECT gid FROM res_groups_users_rel WHERE uid =%s )
                                                order by case when rg.name like 'Admin' then 0 when rg.name like 'Dealer' then 1 else 2 end limit 1 """ ,
                                                ('Dealer',str(current_user.id)))
        groups =request.cr.dictfetchall()
        role =None
        for result in groups:
            role = result['name']
        session_info['role']=role
        return session_info

    @http.route(['/app_api/vmc'], type='json', auth="none")
    def vmc_call_kw(self, *args, **kwargs):
        _logger.debug("请求参数:" + str(kwargs))
        net_status_normal = "网络正常"
        if 'machine_id' in kwargs:
            machine_id = int(kwargs['machine_id'])
            t = time.time()
            tt = time.strftime(r'%Y-%m-%d %H:%M:%S', time.localtime(t))
            last_update_time = utcStrTime(request, tt, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
            request.cr.execute(
                """select net_status, last_update_time
                   from vmc_machines
                   where id = %s
                """, (machine_id,)
            )
            r = request.cr.dictfetchone()
            net_status = r['net_status']
            # last_time = r['last_update_time']
            # time_format = "%Y-%m-%d %H:%M:%S"
            # last_time_struct_time = time.strptime(last_time, time_format)
            # last_time_timestamp = time.mktime(last_time_struct_time)
            # timestamp_interval = t - last_time_timestamp
            if net_status != "网络正常":
                request.cr.execute(
                    """update vmc_machines
                       set net_status = %s,
                           last_update_time = %s
                       where id = %s
                    """, (net_status_normal, last_update_time, machine_id)
                )
                # elif timestamp_interval > 60:  # 数据库记录更新日期为1分钟前
                #     request.cr.execute(
                #         """update vmc_machines
                #            set  last_update_time = %s
                #            where id = %s
                #         """, (last_update_time, machine_id)
                #     )
        result = self.vmc_version_control(self, *args, **kwargs)
        _logger.debug("执行完:" + kwargs['method'])
        return result

    def vmc_version_control(self, *args, **kwargs):
        app_version = kwargs.get('app_version', False)
        _logger.error(app_version)
        if not app_version:
            res = eval("self." + kwargs['method'])(*args, **kwargs)
        else:
            res = eval("self." + kwargs['method'])(*args, **kwargs)

        return res

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

    # 商品列表接口
    @predispatch
    def vmc_machine_product_list(self, *args, **kwargs):
        _logger.debug("Begin vmc_machine_product_list ...")
        stacks_products = []
        if 'machine_id' in kwargs:
            machine_id = kwargs['machine_id']
            machine = request.env['vmc.machines'].sudo().browse(
                int(machine_id)
            )
            stacks = machine.machine_stack
            stacks_ids = stacks.ids
            stacks_products = self._getstackline(machine_id, stacks_ids)
        request.cr.execute(
            """select distinct(name)
               from product_category
               where name not in ('All', 'Saleable','Internal','Services','Physical','Software','全部')
            """
        )
        result = request.cr.fetchall()
        # 商品分类列表
        product_type_list = []
        for i in result:
            product_type_list.append(i[0])  # 列表内多个子列表合并成一个列表
        _logger.debug("End vmc_machine_product_list\n")
        return {"total": len(stacks_products), "records": stacks_products, "product_type_list": product_type_list}

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

    # *** 1 创建补货记录
    def _create_procurement(self, *args, **kwargs):
        _logger.debug("Begin create procurement ...")
        res = {'msg': 'ok'}
        try:
            # 1 创建补货记录
            move_obj = request.env['stock.move'].sudo()
            move_ids = []
            vmc_clear_record_obj = request.env['vmc.clear.record']
            # 售货机ID
            machine_id = kwargs['machine_id']
            uid = kwargs['uid']
            vmc_adjust_stock_id = kwargs['vmc_adjust_stock_id']
            group_id1 = group_id2 = 0
            # 创建补货记录
            vmc_procurement_move_vals = {'vmc_adjust_stock_id': vmc_adjust_stock_id, 'vmc_machines_id': machine_id}
            sql_updatestacks = sql_updatesupplier = sql_updatechange = ''
            for line in kwargs['record']:
                if not group_id1:
                    group_id1 = request.env['procurement.group'].create({'name': kwargs['code'] + '补货'}).id
                product_id = int(line['supply_product']['id'])
                product_id2 = product_id

                # 取得产品单位
                sql = """select uom_id from product_product a
                   left join product_template b on b.id=a.product_tmpl_id where a.id=%s""" % (product_id)
                request.cr.execute(sql)
                uom_id = request.cr.fetchone()[0]
                # 取得产品对应的货道的库位
                sql = """select b.stack_location_id from vmc_machines a
                   left join vmc_machine_stacks b on b.vmc_machine_id=a.id where b.stack_number='%s' and b.box_number ='%s' and b.vmc_machine_id=%s """ % (
                line['stack']['stack_no'], line['stack']['box_no'], machine_id)
                request.cr.execute(sql)
                fet = request.cr.fetchone()
                if not fet:
                    return {'msg': '没有找到货道对应的库位:%s' % (line['stack']['stack_no'])}
                stock_location_id = fet[0]
                # move 字典
                move_vals = {
                    'name': kwargs['code'] + ':补货',
                    'company_id': kwargs['company_id'],
                    'product_id': product_id,
                    'product_uom': uom_id,
                    'product_uom_qty': line['supply_product']['actual_count'],
                    'location_id': kwargs['car_stock_loctaion_id'],
                    'location_dest_id': stock_location_id,
                    'procure_method': 'make_to_stock',
                    'origin': kwargs['code'] + ':补货',
                    'picking_type_id': kwargs['int_type_id'],
                    'date': kwargs['create_date'],
                    'date_expected': kwargs['create_date'],
                    'group_id': group_id1,
                    'warehouse_id': kwargs['common_stock_warehouse_id'],
                    'supply_id': uid,
                    'svm_type': '补货'
                }
                #move_ids += [move_obj.create(move_vals).id]
                if not sql_updatechange:
                    sql_updatechange = """update vmc_swap_order set state='swaped' where
                                       id =(select coalesce(vmc_swap_order_id,0) from vmc_adjust_stock where id=%s);""" % (
                    vmc_adjust_stock_id)
                # 更新售货机货道信息；产品＋数量 注意库存数量不能大于货道容量
                if not line['product_change']:
                    # sql = """select sum(stock),sum(stack_volume) from vmc_machine_stacks where coalesce(stock,0)+%s>stack_volume
                    #         and vmc_machine_id=%s and stack_number='%s' and box_number ='%s';""" % (
                    # line['supply_product']['actual_count'], machine_id, line['stack']['stack_no'],
                    # line['stack']['box_no'])
                    sql = """select sum(stock),sum(stack_volume) from vmc_machine_stacks where %s>stack_volume
                            and vmc_machine_id=%s and stack_number='%s' and box_number ='%s';""" % (
                        line['supply_product']['actual_count'], machine_id, line['stack']['stack_no'],
                        line['stack']['box_no'])
                    request.cr.execute(sql)
                    fet = request.cr.fetchone()
                    if fet[1]:
                        request.cr.rollback()
                        return {'msg': '不换货:货道(%s)对应的补货数量(%s)超过货道容量(%s)' % (
                        line['stack']['stack_no'], line['supply_product']['actual_count'], fet[1])}

                if line['product_change']:
                    sql = """select sum(stock),sum(stack_volume) from vmc_machine_stacks where %s>stack_volume
                             and vmc_machine_id=%s and stack_number='%s' and box_number ='%s' """ % (
                    line['supply_product']['actual_count'], machine_id, line['stack']['stack_no'],
                    line['stack']['box_no'])
                    request.cr.execute(sql)
                    fet = request.cr.fetchone()
                    if fet[1]:
                        request.cr.rollback()
                        return {'msg': '货道(%s)对应的换货数量(%s)超过货道容量(%s)' % (
                            line['stack']['stack_no'], fet[0], fet[1])}

                sql1 = """update vmc_machine_stacks set stock=coalesce(stock,0)+%s where vmc_machine_id=%s and
                          stack_number='%s' and box_number ='%s' ; """ % (
                line['supply_product']['actual_count'], machine_id, line['stack']['stack_no'],
                line['stack']['box_no'])

                # 更新实际补货数量
                sql_updatesupplier += """update vmc_adjust_stock_line set replenish_count_actual=%s where vmc_adjust_stock_id=%s and
                                       vmc_machine_stack_id in (select id from vmc_machine_stacks where stack_number='%s'
                                       and vmc_machine_id=%s and box_number ='%s'); """ % (
                line['supply_product']['actual_count'], vmc_adjust_stock_id, line['stack']['stack_no'], machine_id,
                line['stack']['box_no'])
                # 有换货的情况下，创建清空记录 售货机-->车库位
                if line['product_change']:
                    if not group_id2:
                        group_id2 = request.env['procurement.group'].create({
                            'name': kwargs['code'] + ':换货'}).id
                    if int(line['current_product']['id']):
                        product_id = int(line['current_product']['id'])
                        sql = """select uom_id from product_product a
                           left join product_template b on b.id=a.product_tmpl_id where a.id=%s""" % (product_id)
                        request.cr.execute(sql)
                        uom_id = request.cr.fetchone()[0]
                        if line['current_product']['stock']:
                            move_vals = {
                                'name': kwargs['code'] + ':清空',
                                'company_id': kwargs['company_id'],
                                'product_id': product_id,
                                'product_uom': uom_id,
                                'product_uom_qty': line['current_product']['stock'],
                                'location_dest_id': kwargs['car_stock_loctaion_id'],
                                'location_id': stock_location_id,
                                'procure_method': 'make_to_stock',
                                'origin': kwargs['code'] + ':清空',
                                'picking_type_id': kwargs['int_type_id'],
                                'date': kwargs['create_date'],
                                'date_expected': kwargs['create_date'],
                                'group_id': group_id2,
                                'supply_id': uid,
                                'svm_type': '清空'
                            }
                            #move_ids += [move_obj.create(move_vals).id]

                    #获取商品的原价（当前解决方案），以后从换货单获取（和换货单新需求一起做）
                    request.cr.execute(""" select pt.list_price
                                             from  product_product as pp, product_template as pt
                                              where pp.id=%s and pp.product_tmpl_id = pt.id
                                              """, (product_id2,))
                    product_change_price =request.cr.fetchone()[0]

                    # 更新售货机货道信息；产品＋数量 注意库存数量不能大于货道容量
                    sql1 = """update vmc_machine_stacks set product_id=%s,stock=%s,product_price=%s where vmc_machine_id=%s and
                              stack_number='%s' and box_number ='%s'; """ % (
                    product_id2, line['supply_product']['actual_count'],product_change_price, machine_id, line['stack']['stack_no'],
                    line['stack']['box_no'])
                # sql_updatesupplier +=sql1
                request.cr.execute(sql1)
            if move_ids:

                move_obj.browse(move_ids).action_confirm()
                move_obj.browse(move_ids).force_assign()
                move_obj.browse(move_ids).action_done()
                sql = """update stock_pack_operation set qty_done=product_qty where picking_id in
                         ( select distinct picking_id  as picking_id  from stock_move where id in %s);
                         ----picking与补货单建立联系
                         insert into adjust_move_rel (vmc_adjust_stock_id,stock_move_id)
                         select %s,id  as move_id  from stock_move where id in %s""" % (
                tuple(move_ids + [0, -1]), vmc_adjust_stock_id, tuple(move_ids + [0, -1]),)
                request.cr.execute(sql)
            # 4 更新售货机货道信息；产品＋数量
            if sql_updatestacks:
                request.cr.execute(sql_updatestacks)
            # 5 补货单更新为补货完成，更新实际补货数量
            # supply_date = str(datetime.strftime(datetime.now(), '%Y-%m-%d %H:%M:%S'))
            # sql_updatesupplier += """update vmc_adjust_stock set state='finished',supply_date='%s'  where id=%s;""" % (
            # supply_date, vmc_adjust_stock_id)
            para=json.dumps(kwargs)
            sql_updatesupplier += """update vmc_adjust_stock set state='reslenishment',parameter='%s'  where id=%s;""" % (para,vmc_adjust_stock_id)
            # 6 更新换货单状态为已经换货
            if sql_updatechange:
                sql_updatesupplier += sql_updatechange
            request.cr.execute(sql_updatesupplier)
        except Exception, e:
            request.cr.rollback()
            return {'msg': 'task1:补货完成接口出错(%s)' % str(e)}
        _logger.debug("End vcreate procurement\n")
        return res

    # 补货单完成处理的接口
    @predispatch
    def vmc_supply_complete(self, *args, **kwargs):
        _logger.debug("Begin vmc_replenishment_finished ...")
        _logger.error("开始调用补货完成接口")
        res = {'msg': 'success'}
        # 1 创建补货记录:主仓库－－>车仓库
        # 2 创建清空记录（换货时候：售货机－－>车仓库
        # 3 创建盘点记录（暂时不做）
        # 4 更新售货机货道信息；产品＋数量
        # 5 补货单更新为补货完成，更新实际补货数量
        # 6 换货单更新为已换货（换货时候）
        # 7 同时根据相关记录生成stock move
        vmc_adjust_stock_obj = request.env['vmc.adjust.stock']
        stock_warehouse_obj = request.env['stock.warehouse']
        # 售货机ID
        kwargs['machine_id'] = int(kwargs['machine_id'])
        kwargs['uid'] = int(kwargs['uid']) or request.session.uid or SUPERUSER_ID
        uid = int(kwargs['uid']) or request.session.uid or SUPERUSER_ID
        # vmc_adjust_stock_ids=vmc_adjust_stock_obj.search(request.cr,kwargs['uid'],[('vmc_machine_id', '=', kwargs['machine_id' ]),('state','=','after_taked_delivery')])
        sql = """select id,adjust_stock_code from vmc_adjust_stock where id=%s and state='after_taked_delivery' limit 1""" % (
            int(kwargs['supply_id']))
        request.cr.execute(sql)
        fet = request.cr.fetchone()
        if not fet:
            return {'error': 'error:补货单（%s)没有找到' % (int(kwargs['supply_id']))}
        # 补货单ID,编号
        vmc_adjust_stock_id, code = fet
        # 获取主仓库的库存库位，车仓库的库存库位，主仓库的内部类型
        sql = """with a as (
                          select id,lot_stock_id,int_type_id,company_id,cast(now()-interval'8 hours' as varchar) as create_date from stock_warehouse where stock_type='common'  and
                          company_id=(select company_id from vmc_machines where id=%s) order by id limit 1),
                    b as(
                         select id,lot_stock_id from stock_warehouse where stock_type='car'  and
                         company_id=(select company_id from res_users where id=%s) order by id limit 1)
                         select a.id,b.id,a.lot_stock_id,b.lot_stock_id,a.int_type_id,company_id,create_date from a
                         left join b on 1=1""" % (kwargs['machine_id'], uid)
        request.cr.execute(sql)
        fet = request.cr.fetchone()
        if not fet:
            return {'error': 'error:售货机（%s） 的一般仓库,车库未配置' % kwargs['machine_id']}
        common_stock_warehouse_id, car_stock_warehouse_id, common_stock_loctaion_id, car_stock_loctaion_id, int_type_id, company_id, create_date = fet
        if not common_stock_warehouse_id:
            return {'error': 'error:售货机（%s） 的一般仓库未配置' % kwargs['machine_id']}
        if not car_stock_warehouse_id:
            return {'error': 'error:售货机（%s） 的车库未配置' % kwargs['machine_id']}
        kwargs.update({'common_stock_warehouse_id': common_stock_warehouse_id,
                       'car_stock_warehouse_id': car_stock_warehouse_id,
                       'common_stock_loctaion_id': common_stock_loctaion_id,
                       'car_stock_loctaion_id': car_stock_loctaion_id, 'int_type_id': int_type_id, 'code': code,
                       'vmc_adjust_stock_id': vmc_adjust_stock_id,
                       'company_id': company_id, 'create_date': create_date})
        _logger.error("开始创建补货记录")
        # 1 创建补货记录
        task_1 = self._create_procurement(*args, **kwargs)
        if task_1['msg'] != 'ok':
            request.cr.rollback()
            res = {'error': task_1['msg']}
        _logger.error("创建补货记录完成")
        machine_id = int(kwargs['machine_id'])

        request.cr.execute(
            """
                select stock
                    from vmc_machine_stacks
                    where vmc_machine_id = %s
            """, (machine_id,)
        )

        machine_stacks = request.cr.dictfetchall()
        _logger.error("开始更新售货机库存状态")
        stock_state_color, stock_state = \
            self._get_stock_state(machine_id, machine_stacks)
        request.cr.execute(
            """update vmc_machines
               set stock_state = %s,
               stock_state_color=%s

               where id = %s
            """, (stock_state, 'green', machine_id)
        )
        _logger.error("更新售货机库存状态完成")
        _logger.debug("End vmc_replenishment_finished\n")
        _logger.error("调用接口完成")
        return res
    #将vmc_order写入stock_move
    @predispatch
    def write_stock_move(self, *args, **kwargs):
        _logger.debug("Begin write_stock_move ...")
        try:
            create_date=datetime.strftime(datetime.now(),'%Y-%m-%d %H:%M:%S')
            machine_id = kwargs['machine_id']
            order_id = kwargs['order_id']
            box_number = kwargs['box_number']
            stack_number = tuple(map(lambda x: str(x), [-1] + kwargs['stack_number']))

            group_id1 = False
            uid = request.uid or SUPERUSER_ID

            move_obj = request.env['stock.move']
            sql = """select vm.company_id, warehouse_id ,out_type_id
                          from vmc_machines as vm left join stock_warehouse  as sw
                          on vm.warehouse_id = sw.id where vm.id= '%s'""" % (machine_id,)
            request.cr.execute(sql)
            fet = request.cr.fetchone()
            if  not fet:
                return {"error": u"无法获取必要信息 "}

            company_id , warehouse_id, picking_type_id = fet

            if not company_id:
                return {"error": u"无法获取company_id"}

            if not warehouse_id:
                return {"error": u"无法获取warehouse_id"}

            if not picking_type_id:
                return {"error": u"无法获取picking_type_id"}

            sql = """select product_id, stack_location_id from vmc_machine_stacks where box_number='%s' and stack_number in  %s and vmc_machine_id = '%s' """ % (box_number,stack_number, machine_id)
            request.cr.execute(sql)
            machine_lines = request.cr.fetchall()
            if not machine_lines :
                return {"error": u"无法获取location_id和location_id"}
            move_ids = []
            for line in machine_lines:
                product_id, location_id = line
                product_id=kwargs['product_id']

                if not product_id:
                    return {"error":u"无法获取product_id"}

                if not location_id:
                    return {"error":u"无法获取location_id"}

                uom_id = request.env['product.product'].browse(product_id).uom_id.id

                move_vals = {
                    'name': 'order id' + str(order_id),  # vmc order id
                    'company_id': company_id,
                    'product_id': product_id,
                    'product_uom': uom_id,
                    'product_uom_qty': 1,
                    'location_dest_id': 9,  # 客户库位
                    'location_id': location_id,
                    'procure_method': 'make_to_stock',
                    'origin': 'vmc_order',
                    'picking_type_id': picking_type_id,
                    'date': create_date,
                    'date_expected': create_date,
                    'group_id': group_id1,
                    'warehouse_id': warehouse_id,
                    'svm_type': 'vmc销售订单',
                    'supply_id': uid
                }
                move = move_obj.create(move_vals)
                if move:
                    move.action_confirm()
                    move.force_assign()
                    move.action_done()
        except Exception, e:
            request.cr.rollback()
            _logger.error("vmc_order_id_rollback move.%s"%(str(e)))
            res = {"error": "write_stock_move:" + str(e)}
            return res
        _logger.debug("END vmc_order_stock_move ...")
        return {'msg': 'success'}

    # 同步订单接口
    @predispatch
    def vmc_order_sync(self, *args, **kwargs):
        _logger.debug("Begin vmc_order_sync ...")
        if not kwargs.has_key('app_next_version'):
            kwargs['app_next_version'] = kwargs.get('app_version', '0.0.0')
            return self.vmc_order_sync(*args, **kwargs)
        elif versionCompare(kwargs['app_next_version'], '0.4.0', operational='<'):
            kwargs['app_next_version'] = '0.4.0'
            # 商品料道号处理
            stack_no = kwargs['product']['stack_no']
            stack_no = int(stack_no)
            if stack_no < 100:
                kwargs['product']['box_no'] = '9'
            else:
                kwargs['product']['box_no'] = '11'
                kwargs['product']['stack_no'] = str(stack_no - 100)
            # 促销买赠商品料道号处理
            promotion_stack_no = kwargs.get('promotion_stack_no', -1)
            promotion_stack_no = int(promotion_stack_no)
            if promotion_stack_no == -1:
                kwargs['promotion_box_no'] = '-1'
            elif promotion_stack_no < 100:
                kwargs['promotion_box_no'] = '9'
            else:
                kwargs['promotion_box_no'] = '11'
                kwargs['promotion_stack_no'] = str(promotion_stack_no - 100)
            return self.vmc_order_sync(*args, **kwargs)
        else:
            result = self._vmc_order_sync(*args, **kwargs)
            return result

    def _vmc_order_sync(self, *args, **kwargs):
        product_name = kwargs['product']['name']
        product_id = int(kwargs['product']['id'])
        stack_number = kwargs['product']['stack_no']
        box_number = kwargs['product']['box_no']
        if 'shipping_status' in kwargs:
            shipping_status = kwargs['shipping_status']
        else:
            shipping_status = ''
        current_time = datetime.utcnow()
        create_time = kwargs.get('create_time', False)
        if create_time:
            create_time = datetime.strptime(
                create_time, '%Y/%m/%d %H:%M:%S') - timedelta(hours=8)
        else:
            create_time = None
        temperature = 0
        weather = ""
        if kwargs.get('extras', False):
            if kwargs['extras'].get('weather', False):
                temperature = kwargs['extras']['weather'].get('temp', "0")
                weather = kwargs['extras']['weather'].get('weather', "")

        payment_method = {
            'NONE': '其他',
            'ALIPAY': '支付宝',
            'WECHATPAY': '微信',
            'RMB': '人民币',
            'WANGBI': '旺币',
            'CODE': '提货码'
        }[kwargs['payment_method']]

        payment_status = {
            'PAID': '已支付',
            'UNPAY': '未支付'
        }[kwargs['payment_status']]

        order_status = {
            'CREATED': '创建',
            'CANCEL': '用户取消',
            'PAID': '用户已支付',
            'FINISHED': '订单已完成'
        }[kwargs['status']]

        machine_id = int(kwargs['machine_id'])
        amount_total = float(kwargs['amount']) / 100.00

        # if payment_method=="人民币" and payment_status=="已支付":
        #     request.cr.execute("""
        #         update vmc_machines set total_crash=total_crash+%s where id =%s
        #     """,(amount_total,machine_id))

        request.cr.execute(
            """
                select vmc_code from vmc_machines
                    where id = %s
            """,
            (machine_id,)
        )
        vmc_code = request.cr.dictfetchall()[0]['vmc_code']

        name = kwargs.get('id', False)
        if not name:
            request.cr.execute(
                """
                    select vmc_code from vmc_machines
                        where id = %s
                """,
                (machine_id,)
            )

            vmc_code = request.cr.dictfetchall()[0]['vmc_code']

            name = vmc_code + request.env['ir.sequence'].sudo().next_by_code(
                'vmc.order')
        vmc_order_id = False
        request.cr.execute(
            """
                select * from vmc_order where name = %s
            """,
            (name,)
        )
        vmc_order = request.cr.dictfetchone()
        if vmc_order:
            vmc_order_id = vmc_order['id']
        promotion_id = None
        promotion_type = None
        discounted_price = 0
        gift_id = None

        #add by wu to handle promotion stock
        promotion_stock_need=False
        promotion_stack_number=False
        promotion_stack_no=False
        promotion_id=False





        # 判断是否是促销订单
        if 'promotion_id' and 'promotion_stack_no' and 'promotion_box_no' in kwargs:
            promotion_id = kwargs['promotion_id']
            promotion_stack_number = str(kwargs['promotion_stack_no'])
            promotion_box_number = str(kwargs['promotion_box_no'])
            request.cr.execute("""
                                     select promotion_type, payment_alipay, payment_weixin, payment_cash, payment_wangbi
                                     from vmc_promotion
                                     where id = %s
                                   """, (promotion_id,)
                               )
            r = request.cr.dictfetchone()
            if r is not None:  # 促销 id 存在
                # 促销支持的支付方式
                supported_payment_method = []
                if r['payment_alipay'] is True:
                    supported_payment_method.append('支付宝')
                if r['payment_weixin'] is True:
                    supported_payment_method.append('微信')
                if r['payment_cash'] is True:
                    supported_payment_method.append('人民币')
                if r['payment_wangbi'] is True:
                    supported_payment_method.append('旺币')
                if payment_method in supported_payment_method:  # 容错处理:上报的促销订单支付方式确实为促销政策规定的支付方式
                    promotion_type = r['promotion_type']
                    promotion_type_dict = {
                        'one_more': '买赠',
                        'discount': '折扣',
                        'unchange_count': '立减'
                    }
                    promotion_type = promotion_type_dict[promotion_type]
                    request.cr.execute("""
                                            select product_id
                                            from vmc_machine_stacks
                                            where stack_number = %s and box_number = %s and vmc_machine_id = %s
                                           """, (promotion_stack_number, promotion_box_number, machine_id)
                                       )
                    rr = request.cr.dictfetchone()
                    if rr is not None:
                        gift_id = rr['product_id']  # 赠品 id
                        # request.cr.execute("""
                        #                        update vmc_machine_stacks
                        #                        set stock=stock-1
                        #                        where vmc_machine_id=%s and stack_number=%s and box_number = %s and coalesce(stock,0)>0
                        #                        """,
                        #                    (machine_id, promotion_stack_number, promotion_box_number)
                        #                    )
                        #### fix by wu 修改赠品库存处理 应与商品一起处理 考虑订单支付状态问题
                        promotion_stock_need = True
                    else:  # 买赠订单赠品缺货(-1) 或 订单为立减或折扣订单(-1) 或 找不到售货机对应的料道号
                        gift_id = None
                    # 查询商品原价
                    # request.cr.execute("""
                    #                          select pt.list_price
                    #                          from vmc_machine_stacks as vs, product_product as pp, product_template as pt
                    #                          where vs.product_id = pp.id and pp.product_tmpl_id = pt.id
                    #                            and vs.stack_number = %s and vs.box_number = %s and vs.vmc_machine_id = %s
                    #                          """, (stack_number, box_number, machine_id)
                    #
                    #                    )
                    request.cr.execute("""
                                         select vs.product_price
                                         from vmc_machine_stacks as vs
                                         where vs.stack_number = %s and vs.vmc_machine_id = %s
                                         """, (stack_number, machine_id)
                                      )
                    s = request.cr.dictfetchone()
                    product_price = s['product_price'] or 0
                    # 计算出优惠的价格
                    if 'promotion_stack_no' in kwargs:
                        discounted_price = product_price - amount_total
                    else:
                        discounted_price = 0
            else:  # 数据库找不到促销id
                promotion_id = None
                promotion_type = None
                discounted_price = 0
                gift_id = None
        else:  # 非促销订单
            promotion_id = None
            promotion_type = None
            discounted_price = 0
            gift_id = None

        result = {"msg": "success"}
        #买赠 买A赠A 库存只会处理一次
        if 'promotion_id' in kwargs:
            stack_number_list = [stack_number]
        else:
            stack_number_list = [stack_number]
        _logger.error("before if not vmc_order_id %s...\n " % (name))
        if not vmc_order_id:
            _logger.error("in if not vmc_order_id %s...\n "%(name))
            request.cr.execute("""
                    insert into vmc_order (
                        name,create_time,create_date,product_name,product_id,payment_method,payment_status,
                        stack_number,machine_id,amount_total,order_status,temperature,weather,
                        donation_product_id, promotion_policy, promotion_rules, discounted_prices,shipping_status
                    ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning ID""", (
                name, create_time, current_time, product_name, product_id, payment_method,
                payment_status, stack_number, machine_id, amount_total, order_status,
                temperature, weather, gift_id, promotion_id, promotion_type, discounted_price, shipping_status)
            )
            fet=request.cr.fetchone()
            if not fet:
                _logger.error("in error vmc_order_id.%s.\n " % (name))
                return {'error': '创建失败'}
            vmc_order_id=fet[0]
            ###add by wu 加上赠品库存处理
            res_stock=self.change_stock_and_cash(
                name, payment_method, payment_status, amount_total,
                machine_id, box_number, vmc_order_id, stack_number,
                stack_number_list,promotion_stock_need,promotion_stack_number,promotion_box_number,gift_id,product_id,shipping_status)
            if not res_stock:
                _logger.error("create error vmc_order_id.%s.\n " % (name))
                return {'error':'创建失败'}

        else:
            _logger.error("in else vmc_order_id.%s.\n " % (name))
            if not (
                vmc_order['payment_method'] == payment_method and
                vmc_order['payment_status'] == payment_status
            ):
                ###add by wu 加上赠品库存处理
                self.change_stock_and_cash(
                    name, payment_method, payment_status, amount_total,
                    machine_id, box_number, vmc_order_id, stack_number,
                    stack_number_list,promotion_stock_need,promotion_stack_number,promotion_box_number,gift_id,product_id,shipping_status)

            request.cr.execute(
                """
                    update vmc_order set
                        create_time = %s,
                        create_date = %s,
                        product_name = %s,
                        product_id = %s,
                        payment_method = %s,
                        payment_status = %s,
                        stack_number = %s,
                        machine_id = %s,
                        amount_total = %s,
                        order_status = %s,
                        temperature = %s,
                        weather = %s,
                        donation_product_id = %s,
                        promotion_policy = %s,
                        promotion_rules = %s,
                        discounted_prices = %s,
                        shipping_status = %s
                    where id = %s
                """,
                (
                    create_time, current_time, product_name, product_id,
                    payment_method, payment_status,
                    stack_number, machine_id, amount_total,
                    order_status, temperature, weather,
                    gift_id, promotion_id, promotion_type, discounted_price,
                    shipping_status, vmc_order_id
                )
            )

        request.env['bus.bus'].sendone(
            'auto_refresh_vmc_dashboard', 'vmc.order')
        result.update({'vmc_order_id':vmc_order_id})
        _logger.debug("End vmc_order_sync ...\n")
        return result

    # 根据上报的订单信息来操作库存以及售货机现金总额
    def change_stock_and_cash(
        self, name, payment_method, payment_status, amount_total,
        machine_id, box_number, order_id, stack_number,
        stack_number_list,promotion_stock_need,promotion_stack_number,promotion_box_number,gift_id,product_id,shipping_status
    ):
        if payment_method == "人民币" and payment_status == "已支付":
            sql = """update vmc_machines set total_crash=total_crash+%s where id =%s;
                            """ % (amount_total, machine_id)
            request.cr.execute(sql)
            # 根据订单状态更新售货机的料道存量，存在用户已支付未出货不更新料道存量的问题
        #if payment_status == "已支付":
        # if payment_status == "已支付" or (payment_method=="人民币" and shipping_status == "false"):#如果是人民币没有投钱 payment_status 传过来是 空，如果是false未支付，那么就是人民币卡货，需要减少库存
        if shipping_status != '':  # 如果是人民币没有投钱 payment_status 传过来是 空，如果是false未支付，那么就是人民币卡货，需要减少库存
            request.cr.execute("""
                           update vmc_machine_stacks set stock=stock-1
                               where vmc_machine_id=%s
                               and stack_number = %s
                               and box_number= %s
                               and coalesce(stock,0)>0
                        """, (machine_id, stack_number, box_number))
            kwargs = {
                "machine_id": machine_id,
                "order_id": order_id,
                "stack_number": stack_number_list,
                "box_number":box_number,
                "product_id":product_id
            }
            result_move=self.write_stock_move(**kwargs)
            if result_move.get('error'):
                _logger.error("error vmc_order_id get error in vmc_order_stock_move ...")
                return False
            #处理赠品库存
            if promotion_stock_need and promotion_stack_number and promotion_box_number and gift_id and shipping_status=='true':
                request.cr.execute("""
                               update vmc_machine_stacks set stock=stock-1
                                   where vmc_machine_id=%s
                                   and stack_number = %s
                                   and box_number= %s
                                   and coalesce(stock,0)>0 and product_id=%s
                            """, (machine_id, promotion_stack_number, promotion_box_number,gift_id))

                kwargs = {
                    "machine_id": machine_id,
                    "order_id": order_id,
                    "stack_number": [promotion_stack_number],
                    "box_number":promotion_box_number,
                    "product_id":gift_id
                }
                result_move = self.write_stock_move(**kwargs)
                if result_move.get('error'):
                    _logger.error("error vmc_order_id get error in vmc_order_stock_move ...")
                    return False

        return True

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
        # 屏蔽库存上报写入售货机的料道存量
        # for s in stocks:
        #     stock = int(s['remain_qty'])
        #     if stock >= 0:
        #         product_id = int(s['product']['id'])
        #         stack_number = s['product']['stack_no']
        #         request.cr.execute(
        #             """
        #             update vmc_machine_stacks set stock = %s
        #                 where vmc_machine_id = %s and stack_number = %s
        #                     and product_id = %s
        #             """,
        #             (stock, machine_id, stack_number, product_id,))

        # error_info_color = 'red'
        # if kwargs['machine']['status'] == '设备正常':
        #     error_info_color = 'green'

        # if kwargs['machine']['status'] == '设备正常':
        #     error_info_color = 'green'
        result = {"msg": "success"}
        _logger.debug("End vmc_stock_sync ...\n")
        return result

    def _product_stock_out(self, request, **kwargs):
        if kwargs['payment_status'] != 'PAID':
            return

        request.cr.execute(
            """
                select stack_location_id,product_id
                    from vmc_machine_stacks
                    where  vmc_machine_id = %s
                        and stack_number = %s
            """, (
                int(kwargs['machine_id']),
                kwargs['product']['stack_no']
            )
        )

        vmc_machines_stacks = request.cr.dictfetchall()

        if len(vmc_machines_stacks) > 0:
            request.cr.execute(
                """
                    select id,qty
                        from stock_quant
                        where  location_id = %s
                            and product_id = %s
                """, (
                    vmc_machines_stacks[0]['stack_location_id'],
                    vmc_machines_stacks[0]['product_id']
                )
            )

            stock_quants = request.cr.dictfetchall()

            if len(stock_quants) > 0:
                request.cr.execute(
                    """
                        update stock_quant set qty = %s
                            where  id = %s
                    """, (
                        stock_quants[0]['qty'] - 1,
                        stock_quants[0]['id']
                    )
                )

    def _update_stock_status(self, request, **kwargs):
        machine_id = int(kwargs['machine_id'])

        request.cr.execute(
            """
                select stock
                    from vmc_machine_stacks
                    where vmc_machine_id = %s
            """, (machine_id,)
        )

        machine_stacks = request.cr.dictfetchall()

        stock_state_color, stock_state = \
            self._get_stock_state(machine_id, machine_stacks)

        request.cr.execute(
            """
                update vmc_machines set
                    stock_state = %s,
                    stock_state_color = %s
                    where id = %s
            """, (
                stock_state, stock_state_color,
                int(kwargs['machine_id']),
            )
        )

    def _get_stock_state(self, machine_id, machine_stacks):
        if len(machine_stacks) == 0:
            return 'red', '需补货'

        request.cr.execute(
            """
                select warn_qty
                    from vmc_machines
                    where id = %s
            """,
            (machine_id,)
        )

        warn_qty = request.cr.dictfetchall()[0]['warn_qty']

        total_stock = 0

        for stack in machine_stacks:
            if not stack['stock']:
                return 'red', '需补货'

            total_stock += stack['stock']

        if total_stock <= 0:
            return 'red', '需补货'

        if total_stock < warn_qty:
            return 'orange', '库存低'

        return 'green', '正常'

    # 获取料道商品详情信息
    def _getstackline(self, machine_id, stacks_ids, is_gift=False):
        model = "vmc.machine.stacks"
        fields = [
            "product_id",
            "stack_number",
            "box_number",
            "stock",
            "product_sequence_no",
            # "channel_no"
        ]
        domain = [('id', 'in', stacks_ids)]
        offset = 0
        limit = False
        sort = "product_id desc"
        stacks = self.search_read(model, fields, offset, limit, domain, sort)
        stacks_products = []
        for stack in stacks['records']:
            if stack['product_id']:
                record = {}
                stack_id = stack['id'] or 0
                product = request.env['product.product'].sudo().browse(
                    stack['product_id'][0])
                record['id'] = stack['product_id'][0] or ""
                record['name'] = stack['product_id'][1] or ""
                record['stack_no'] = stack['stack_number'] or ""
                record['box_no'] = stack['box_number'] or ""
                host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                host_url_replaced = host_url_replaced.replace(':88', '')
                record['image_url'] = host_url_replaced + image_url(
                    product.product_tmpl_id, 'image', '300x300')
                record['stock'] = stack['stock'] or 0
                # 商品原价
                request.cr.execute("""
                                     select vs.product_price
                                     from vmc_machine_stacks as vs
                                     where vs.stack_number = %s and vs.box_number= %s and vs.vmc_machine_id = %s
                                     """, (stack['stack_number'],stack['box_number'], machine_id)
                   )
                s = request.cr.dictfetchone()
                product_price = s['product_price'] or 0
                product_price = int(product_price * 100)
                record['price'] = product_price
                record['seq_no'] = stack['product_sequence_no'] or ""
                # 商品类别,饮品还是食品
                record['product_type'] = product.product_tmpl_id.categ_id.name or ""
                # 净含量
                record['net_weight'] = product.product_tmpl_id.net_weight or ""
                # record['channel_no'] = stack['channel_no'] or ""
                # 数据库得到的字段, 包含 商品详情图片url 的html字符串
                product_details_html_info = product.product_tmpl_id.product_describe
                # 对字符串切片, 拿到图片的url
                if product_details_html_info:
                    index_start = product_details_html_info.find('/') + 1  # 从斜杠之后开始切片
                    index_end = product_details_html_info.find('.jpg')
                    if index_end <> -1:
                        product_details_image_url = product_details_html_info[index_start:index_end + offset]
                    else:
                        # product_details_image_url = product_details_html_info[index_start:]
                        image_list = product_details_html_info.split('"')
                        if image_list and len(image_list) > 1 and image_list[1] != None and len(image_list[1]) > 1:
                            product_details_image_url = image_list[1][1:]
                        else:
                            record['product_details_image_url'] = ''
                            product_details_image_url = ''
                    host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                    host_url_replaced = host_url_replaced.replace(':88', '')
                    record['product_details_image_url'] = (
                        host_url_replaced + product_details_image_url)
                else:
                    record['product_details_image_url'] = ''
                # 促销详情
                product_id = stack['product_id'][0]
                request.cr.execute(
                    """
                        select vp.id as promotion_id, vp.promotion_type,vp.is_promotional_image_links,vp.promotional_image_links, vp.name, vp.discount_rate,
                               vp.promotion_time_type, vp.start_time, vp.end_time, vp.start_date, vp.period_of_start_date, vp.period_of_end_date, vp.end_date,
                               vp.payment_cash, vp.payment_weixin, vp.payment_alipay, vp.payment_wangbi, vp.unchange_price
                        from vmc_promotion as vp, product_product as pp, product_template as pt,
                             vmc_product_promotion_rel as pl, vmc_machines as vm,vmc_sales_machines_list as vl
                        where pp.id = %s and vp.start_using = True and pl.product_id = pp.id and pp.product_tmpl_id = pt.id
                          and pl.vmc_select_id = vp.id and vl.machine_id = vm.id and vl.vmc_sales_id = vp.id and vm.id = %s
                    """,
                    (product_id, machine_id)
                )
                all_promotion_record = request.cr.dictfetchall()
                current_time = datetime.utcnow() - timedelta(hours=-8)
                current_time_str = datetime.strftime(current_time, "%H:%M:%S")
                current_date_str = datetime.strftime(current_time, "%Y-%m-%d")
                utc_current_time = datetime.utcnow()
                utc_current_timedate_str = datetime.strftime(utc_current_time, "%Y-%m-%d %H:%M:%S")
                # 遍历所有的促销,找出还未过期的促销
                active_promotion_record = []
                for single_promotion_record in all_promotion_record:
                    if single_promotion_record['end_date'] is not None:
                        if current_date_str > single_promotion_record['end_date'] or (current_date_str == single_promotion_record['end_date'] and current_time_str >= single_promotion_record['end_time']):
                            pass
                        else:
                            active_promotion_record.append(single_promotion_record)
                    elif single_promotion_record['period_of_end_date'] is not None:
                        if utc_current_timedate_str >= single_promotion_record['period_of_end_date']:
                            pass
                        else:
                            active_promotion_record.append(single_promotion_record)
                # 对促销按时间早晚排序
                for single_record in active_promotion_record:
                    if single_record['start_time'] and single_record['start_date'] is not None:
                        single_record['key_for_sort'] = single_record['start_date'] + ' ' + single_record['start_time']
                    elif single_record['period_of_start_date'] is not None:
                        single_record['key_for_sort'] = single_record['period_of_start_date']
                active_promotion_record.sort(key=lambda t: t['key_for_sort'], reverse=False)
                # 促销详情字段
                record['promotion_details'] = {}
                if len(active_promotion_record) != 0:
                    # 取时间最近的那个促销
                    r = active_promotion_record[0]
                    # 转换成utc时间
                    time_format = "%Y-%m-%d %H:%M:%S"
                    time_period_start = r['period_of_start_date']
                    time_period_end = r['period_of_end_date']
                    if time_period_start is not None:
                        time_period_start = localizeStrTime(request, time_period_start, time_format, time_format)
                    else:
                        time_period_start = ""
                    if time_period_end is not None:
                        time_period_end = localizeStrTime(request, time_period_end, time_format, time_format)
                    else:
                        time_period_end = ""
                    # 促销弹窗图片链接
                    promotional_image_links = r['promotional_image_links'] if r['is_promotional_image_links'] == '是' else ''
                    # 折扣
                    if r['promotion_type'] == "discount":
                        discount_rate = float(r['discount_rate']) / 100
                        promotion_price = product_price * discount_rate
                    # 立减
                    elif r['promotion_type'] == 'unchange_count':
                        price_variance = int(r['unchange_price'] * 100)  # 立减的价格数值
                        promotion_price = product_price - price_variance
                    # 买赠
                    else:
                        promotion_price = 0
                    # 可选支付方式
                    payment_option = []
                    if r['payment_alipay']:
                        payment_option.append('ALIPAY')
                    if r['payment_weixin']:
                        payment_option.append('WECHATPAY')
                    if r['payment_cash']:
                        payment_option.append('RMB')
                    if r['payment_wangbi']:
                        payment_option.append('WANGBI')
                    # 可选支付方式 过渡方案
                    alipay = '1' if r['payment_alipay'] else '0'
                    weixin = '1' if r['payment_weixin'] else '0'
                    cash = '1' if r['payment_cash'] else '0'
                    wangbi = '1' if r['payment_wangbi'] else '0'
                    payment_way = alipay + weixin + cash + wangbi
                    promotion_time_type = 'every_day' if r['promotion_time_type'] == '1' else 'cross_day'  # 促销时间类型
                    promotion_details = {
                        "promotion_type": r["promotion_type"],
                        "name": r["name"],
                        "promotion_id": r['promotion_id'] or 0,
                        "promotion_time_type": promotion_time_type,
                        "start_date": r["start_date"] or "",
                        "end_date": r["end_date"] or "",
                        "start_time": r["start_time"] or "",
                        "end_time": r["end_time"] or "",
                        "time_period_start": time_period_start,
                        "time_period_end": time_period_end,
                        "promotion_price": promotion_price,
                        "payment_way": payment_way,
                        "payment_option": payment_option,
                        "promotional_image_links": promotional_image_links,
                    }
                    # 买赠商品详情
                    freebie = []
                    promotion_id = r['promotion_id']
                    request.cr.execute(
                        """
                        select pt.name, pt.net_weight, vms.product_id as id, vms.product_sequence_no as seq_no, vms.stock,
                               vms.stack_number as stack_no, vms.box_number as box_no
                        from vmc_promotion as vp, vmc_promotion_lines as vpl, product_product as pp,
                             vmc_machine_stacks as vms, vmc_machines as vm, product_template as pt,
                             vmc_product_promotion_rel as pl, vmc_sales_machines_list as vl
                        where pl.product_id = %s and pl.vmc_select_id = vp.id and vpl.promotion_id = vp.id
                          and vpl.product_id = vms.product_id and vms.vmc_machine_id = vm.id and vpl.product_id = pp.id and pp.product_tmpl_id = pt.id
                          and vl.machine_id = vm.id and vl.vmc_sales_id = vp.id
                          and vm.id = %s and vp.start_using = True and vp.id = %s""",
                        (product_id, machine_id, promotion_id)
                    )
                    s = request.cr.dictfetchall()
                    if s:
                        freebie = s
                    record['promotion_details'].update({
                        "freebie": freebie
                    })
                    record['promotion_details'].update(promotion_details)
                stacks_products.append(record)
        return stacks_products

    # 根据product_id获取产品口味
    def _get_product_flavor(self, product_id):
        try:
            request.cr.execute("""
                    select d.name from product_attribute_line a

                    left join product_attribute_line_product_attribute_value_rel b on b.product_attribute_line_id=a.id

                    left join product_attribute_value_product_product_rel c on c.product_attribute_value_id=b.product_attribute_value_id

                    left join product_attribute_value d on d.id=b.product_attribute_value_id

                    left join product_product e on e.id=c.product_product_id

                    where c.product_product_id=%s and e.product_tmpl_id=a.product_tmpl_id """,(product_id,)
                )
            flavor_info=request.cr.dictfetchone()
            if flavor_info:
                flavor = flavor_info["name"]
            else:
                flavor = "原味"
        except Exception, e:
            flavor = "原味"
        return flavor

    # 根据machine_id获取该售货机补货单详情
    def _getsupply_order(self, app_version, machine_id, state, operator_id, sql_extra):
        records = []
        result = {}
        sql_main = """
                    select m.name as p_unit,n.name as c_unit ,c.street2,c.street,j.name as country,k.name as city,l.name as county,b.factory_code,
                         b.coordinate as location,e.stack_volume,i.name as c_name,a.id as supply_id,a.vmc_swap_order_id,
                         g.id,g.name as p_name,coalesce(e.product_price, 0) as list_price,f.product_tmpl_id ,e.box_number,e.stack_number,c.name,e.product_id,
                         d.adjust_product_id,e.stock,d.replenish_demand,d.replenish_count_actual
                    from vmc_adjust_stock a
                    left join vmc_machines b on b.id=a.vmc_machine_id
                    left join res_partner c on c.id=b.partner_id
                    left join vmc_adjust_stock_line d on d.vmc_adjust_stock_id=a.id
                    left join vmc_machine_stacks e on e.id=d.vmc_machine_stack_id
                    left join product_product f on f.id=d.adjust_product_id
                    left join product_template g on g.id=f.product_tmpl_id
                    left join product_uom m on g.uom_id=m.id
                    left join product_product h on h.id=e.product_id
                    left join product_template i on i.id=h.product_tmpl_id
                    left join product_uom n on i.uom_id=n.id
                    left join res_country_state j on j.id=c.state_id
                    left join res_country_state_city k on k.id=b.city_id
                    left join res_country_state_city_county l on l.id= b.county
                    where a.vmc_machine_id= %s and a.state in %s and d.vmc_machine_stack_id is not null
                """
        sql = sql_main + sql_extra
        request.cr.execute(sql, (machine_id, tuple(state),))
        supply_info = request.cr.dictfetchall()
        if supply_info:
            # 该售货机需要补货的料道数
            total = len(supply_info)
            # 补货单id和编号
            supply_id = supply_info[0]["supply_id"]
            # 售货机信息
            machine_id = machine_id
            machine_obj = {}
            machine_obj["id"] = machine_id
            machine_obj["name"] = supply_info[0]["name"]
            machine_obj["coordinate"] = supply_info[0]["location"]
            machine_obj["address"] = (supply_info[0]["city"] or "") + (supply_info[0]["county"] or "") + (
            supply_info[0]["street2"] or "")
            machine_obj["factory_code"] = supply_info[0]["factory_code"]
            # 对于每个料道的补货信息设置
            sub_records = []
            for i in range(0, total):
                if not supply_info[i]["adjust_product_id"]:
                    continue
                record_item = {
                    "stack": {}, "current_product": {}, "supply_product": {}}
                # 判断该料道是否换货，换货则需要原商品信息
                if supply_info[i]["product_id"] == supply_info[i]["adjust_product_id"]:
                    record_item = {
                        "stack": {}, "supply_product": {}}
                    record_item["product_change"] = 0
                else:
                    record_item["product_change"] = 1
                    record_item["current_product"][
                        "id"] = supply_info[i]["product_id"] or 0
                    record_item["current_product"][
                        "stock"] = supply_info[i]["stock"]
                    record_item["current_product"][
                        "name"] = supply_info[i]["c_name"]
                    record_item["current_product"]["unit"] = supply_info[i]["c_unit"] or ""
                    record_item["current_product"]["flavor"] = self._get_product_flavor(
                        supply_info[i]["product_id"])
                # 货柜和料道号
                record_item["stack"]["stack_no"] = supply_info[i]["stack_number"] or ""
                record_item["stack"][
                    "box_no"] = supply_info[i]["box_number"] or "0"
                if record_item["stack"]["box_no"]=='9':
                    record_item["stack"]["box_name"]='食品柜'
                elif record_item["stack"]["box_no"]=='11':
                    record_item["stack"]["box_name"]='饮料柜'
                else:
                    record_item["stack"]["box_name"]='货柜'

                # 补货商品信息设置
                record_item["supply_product"][
                    "actual_count"] = supply_info[i]["replenish_count_actual"] or "0"
                record_item["supply_product"][
                    "id"] = supply_info[i]["adjust_product_id"] or 0
                record_item["supply_product"][
                    "supply_count"] = supply_info[i]["replenish_demand"] or 0
                record_item["supply_product"][
                    "remaining_count"] = supply_info[i]["stock"] or 0
                record_item["supply_product"][
                    "name"] = supply_info[i]["p_name"] or ""
                product_obj = request.env['product.product'].sudo().browse(
                    supply_info[i]["adjust_product_id"])
                host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                host_url_replaced = host_url_replaced.replace(':88', '')
                record_item["supply_product"]["image_url"] = host_url_replaced + image_url(
                    product_obj.product_tmpl_id, 'image', '300x300')
                record_item["supply_product"][
                    "price"] = int(
                    product_obj.product_tmpl_id.lst_price * 100) or 0
                record_item["supply_product"]["unit"] = supply_info[i]["p_unit"] or ""
                record_item["supply_product"]["flavor"] = self._get_product_flavor(
                    supply_info[i]["adjust_product_id"]) or ""
                record_item["supply_product"]["stack_volume"] = supply_info[i]["stack_volume"]
                # 补货需求为0的商品过滤
                if record_item["supply_product"]["supply_count"] == 0:
                    total = total - 1
                else:
                    # 所有料道的补货信息合成 为该售货机的补货信息
                    sub_records.append(record_item)
                    # result = {"machine_name": supply_info[0][
                    #     "name"], "total": total, "records": records}
            # 补货单补硬币状态, 补货单提款状态
            status = self._get_vmc_money_record(app_version, machine_id, supply_id)
            # 查看补货单是否已补货
            if self._is_supply_order_finished(supply_id):
                status.update({
                    "supplyment_status": 1
                })
            else:
                status.update({
                    "supplyment_status": 0
                })
            # 补货时间
            request.cr.execute("""
                                    select supply_date
                                    from vmc_adjust_stock
                                    where id = %s
                                      """, (int(supply_id),))
            r = request.cr.dictfetchone()
            if r is not None:
                supply_date = r['supply_date']
                if supply_date is not None:
                    time_format = "%Y-%m-%d %H:%M:%S"
                    supply_date = localizeStrTime(request, supply_date, time_format, time_format)
                else:
                    supply_date = ""
            else:
                supply_date = ""
            # 补货员手中剩余商品数量
            products_left = self._products_in_hand(operator_id)
            result = {"supply_date": supply_date, "total": total, "machine": machine_obj, "records": sub_records,
                      "supply_id": supply_id, "status": status, "products_left": products_left}
        return result

    # 补货员 已补货或补货中状态但还未提款的补货单
    def _getsupply_order_not_withdrawn(self, app_version, operator_id):
        _logger.debug("Begin  vmc_history_supply ...")
        resutlt = []
        state = ['finished', 'reslenishment']
        # 获取补货单为完成状态或补货中状态但还未补款的机器id
        request.cr.execute("""
                        select distinct vmc_machine_id, id
                        from vmc_adjust_stock a
                        where a.supply_operator=%s and state in %s and is_withdrawn = False
                          """, (operator_id, tuple(state))
                           )
        fet = request.cr.dictfetchall()
        total = len(fet)
        if fet:
            # return {"message":"没有"}
            # machine_id_list=[0]
            for m in fet:
                # machine_id_list.append(m["vmc_machine_id"])
                request.cr.execute("""
                                select vmc_machines_id,sum(amount),description
                                    from vmc_money_record b where b.create_uid=%s
                                    and b.create_date>=current_date-interval'8 hour' and b.create_date<current_date+interval'16 hour'
                                    and vmc_machines_id =%s
                                    group by description,vmc_machines_id""", (operator_id, m["vmc_machine_id"]))
                money_info = request.cr.dictfetchall()
                sql_extra = " and a.is_withdrawn = False"
                result_item = self._getsupply_order(app_version, m["vmc_machine_id"], state, operator_id, sql_extra)
                result_item["financial"] = {"take_amount": "", "supply_amount": ""}
                for x in money_info:
                    if x["description"] == 'takeout':
                        result_item["financial"]["take_amount"] = x["sum"] or "0"
                    if x["description"] == "addin":
                        result_item["financial"]["supply_amount"] = x["sum"] or "0"
                resutlt.append(result_item)
        _logger.debug("End vmc_history_supply ...")
        return resutlt

    # 获取补货单对应的补硬币记录, 取款记录
    def _get_vmc_money_record(self, app_version, machine_id, supply_id):
        if versionCompare(app_version, '0.4.3', operational='<'):
            status = self._get_vmc_money_record_first_version(machine_id, supply_id)
        else:
            status = self._get_vmc_money_record_second_version(machine_id, supply_id)
        return status

    # 获取补货单对应的补硬币记录, 取款记录(新版第二版接口)
    def _get_vmc_money_record_second_version(self, machine_id, supply_id):
        request.cr.execute("""
                                    select payment_cash
                                    from vmc_machines
                                    where id = %s
                                    """, (machine_id,)
                           )
        result = request.cr.dictfetchone()
        is_cash_function_supported = result['payment_cash']
        if is_cash_function_supported:
            is_cash_function_supported = 1
            supply_id = str(supply_id)
            request.cr.execute("""
                                            select sum(amount) as coin_supplement_amount
                                            from vmc_money_record
                                            where adjust_stock_id = %s and type = 'giveby_supply'
                                            """, (supply_id,)
                               )
            r = request.cr.dictfetchone()
            coin_supplement_amount = r['coin_supplement_amount']
            if coin_supplement_amount is not None:
                coin_supplement_status = 1
                coin_supplement_amount = r['coin_supplement_amount']
            else:
                coin_supplement_status = 0
                coin_supplement_amount = 0
            # 查询补货单是否提款
            request.cr.execute("""
                                              select *
                                              from vmc_money_record
                                              where adjust_stock_id = %s and type = 'get'
                                           """, (supply_id,)
                               )
            r = request.cr.dictfetchone()
            if r is not None:
                money_withdrawl_status = 1
                # 提款额
                money_withdrawl_amount = r["amount"]
            else:
                money_withdrawl_status = 0
                money_withdrawl_amount = 0
            status = {
                "coin_supplement_status": coin_supplement_status,
                "coin_supplement_amount": coin_supplement_amount,
                "money_withdrawl_status": money_withdrawl_status,
                "money_withdrawl_amount": money_withdrawl_amount,
                "is_cash_function_supported": is_cash_function_supported,
            }
        else:
            is_cash_function_supported = 0
            status = {
                "coin_supplement_status": 2,  # 2为售货机不支持现金功能标识
                "coin_supplement_amount": 0,
                "money_withdrawl_status": 2,
                "money_withdrawl_amount": 0,
                "is_cash_function_supported": is_cash_function_supported,
            }
        return status

    # 获取补货单对应的补硬币记录, 取款记录(旧版第一版接口)
    def _get_vmc_money_record_first_version(self, machine_id, supply_id):
        supply_id = str(supply_id)
        request.cr.execute("""
                            select sum(amount) as coin_supplement_amount
                            from vmc_money_record
                            where adjust_stock_id = %s and type = 'giveby_supply'
                            """, (supply_id,)
                           )
        r = request.cr.dictfetchone()
        coin_supplement_amount = r['coin_supplement_amount']
        if coin_supplement_amount is not None:
            coin_supplement_status = 1
        else:
            coin_supplement_status = 0
            coin_supplement_amount = 0
        # 查询补货单是否提款
        request.cr.execute("""
                              select *
                              from vmc_money_record
                              where adjust_stock_id = %s and type = 'get'
                           """, (supply_id,)
                           )
        r = request.cr.dictfetchone()
        if r is not None:
            money_withdrawl_status = 1
            # 提款额
            money_withdrawl_amount = r["amount"]
        else:
            money_withdrawl_status = 0
            money_withdrawl_amount = 0
        status = {
            "coin_supplement_status": coin_supplement_status,
            "coin_supplement_amount": coin_supplement_amount,
            "money_withdrawl_status": money_withdrawl_status,
            "money_withdrawl_amount": money_withdrawl_amount,
        }
        return status

    # 补货单状态, 是否已补货
    def _is_supply_order_finished(self, supply_id):
        supply_id = str(supply_id)
        state = ('finished', 'reslenishment')
        request.cr.execute("""
                                  select id
                                  from vmc_adjust_stock
                                  where id = %s and state in %s
                               """, (supply_id, state)
                           )
        r = request.cr.dictfetchone()
        if r is not None:
            return True
        else:
            return False

    def _products_in_hand(self, operator_id):
        # 提货完成的补货单列表
        request.cr.execute("""
                          select adjust_stock_code
                          from vmc_adjust_stock
                          where state = 'after_taked_delivery' and supply_operator = %s
                          """, (operator_id,)
                           )
        r = request.cr.dictfetchall()
        products_left = []
        # 补货员手中剩余商品数量
        for s in r:
            code = s['adjust_stock_code']
            code = '%' + code + '%'
            word = '%' + '提货' + '%'
            request.cr.execute("""
                                  select mo.product_id, mo.product_uom_qty as count, pt.name as product_name
                                  from stock_move as mo, product_product as pp, product_template as pt
                                  where mo.product_id = pp.id and pp.product_tmpl_id = pt.id and mo.name ilike %s and mo.name ilike %s
                                   """, (code, word)
                               )
            result = request.cr.dictfetchall()
            if len(products_left) == 0:
                products_left = result
            else:
                for p in result:
                    for i in products_left:
                        if p['product_id'] == i['product_id']:
                            i['count'] += p['count']
                            break
                    else:
                        products_left.append(p)
        return products_left

    # 获取补货单 接口
    @predispatch
    def vmc_supply_list(self, *args, **kwargs):
        _logger.debug("Begin vmc_supply_list ...")
        # try:
        machine_id = int(kwargs.get('machine_id') or 0)
        uid = kwargs.get('uid')
        app_version = kwargs.get('app_version', '')
        operator_id = uid
        mark = kwargs.get('supply_status')
        if mark == 0:
            state = ['confirm', 'on_taking_delivery']
        else:
            state = ['after_taked_delivery', '0']
        result = []
        # sql_extra为调用的方法必需的参数,
        sql_extra = ''
        if uid:
            request.cr.execute("""
                        select vmc_machine_id from vmc_adjust_stock where supply_operator= %s and state in %s
                      """, (uid, tuple(state)))
            machine_ids = request.cr.dictfetchall()
            machine_count = len(machine_ids)
            for m in machine_ids:
                machine_supply_order = self._getsupply_order(app_version, m["vmc_machine_id"], state, operator_id, sql_extra)
                # 补货单信息获取失败，直接返回错误信息
                if machine_supply_order.get('error'):
                    return machine_supply_order
                result.append(machine_supply_order)
            if mark == 1:
                r = self._getsupply_order_not_withdrawn(app_version, operator_id)  # 已补货但未提款的补货单
                for i in r:
                    result.append(i)
        else:
            machine_supply_order = self._getsupply_order(app_version, machine_id, state, operator_id, sql_extra)
            result.append(machine_supply_order)
            machine_count = 1
        _logger.debug("END vmc_supply_list ...")
        return {"records": result, "total": machine_count}

    # 货道配置同步接口
    @predispatch
    def vmc_stack_sync(self, *args, **kwargs):
        _logger.debug("Begin vmc_stack_sync ...")
        # try:
        if not kwargs.get('machine_id'):
            return {'error': '缺少机器ID'}
        machine_id = int(kwargs['machine_id'])
        try:
            sql = """select id from vmc_adjust_stock where vmc_machine_id=%s and state='finished' and coalesce(machine_sync,'')<>'已同步' limit 1""" % machine_id
            request.cr.execute(sql)
            fet = request.cr.fetchone()
            if not fet:
                return {'error': '机器：%s 不能找到满足条件的补货单' % machine_id}
            supply_id = fet[0]
            sql = """select a.id as sync_id, a.vmc_swap_order_id, coalesce(b.box_number,'1') as box_no,
                            coalesce(b.stack_number,'') as stack_no,coalesce(b.product_sequence_no,'') as seq_no,
                            b.product_id as id,coalesce(b.stock,0) as stock,
                            coalesce(b1.replenish_count_actual,0) as stock_supplied,
                            trunc(coalesce(b.product_price, 0)*100) as price,d.name
                     from vmc_adjust_stock a
                     left join vmc_adjust_stock_line b1 on b1.vmc_adjust_stock_id=a.id
                     left join vmc_machine_stacks b on b.id=b1.vmc_machine_stack_id
                     left join product_product c on c.id=b.product_id
                     left join product_template d on d.id=c.product_tmpl_id
                     where a.id=%s""" % supply_id
            request.cr.execute(sql)
            info_list = request.cr.dictfetchall()
            if not info_list:
                return {'error': '没有需要同步的产品明细'}
            res = {'sync_id': info_list[0]['sync_id']}
            records = []
            # 查询补货单是否有换货
            sql = """select a.id, a.vmc_swap_order_id
                     from vmc_adjust_stock a
                     where a.id=%s""" % supply_id
            request.cr.execute(sql)
            result = request.cr.dictfetchone()
            swap_order_id = result['vmc_swap_order_id']
            if swap_order_id is not None:
                sql = """select b.stack_number as stack_no, b.box_number as box_no, a.swap_product_id, a.origin_product_id, a.type
                         from vmc_swap_order_line as a
                         left join vmc_machine_stacks as b on b.id=a.vmc_machine_stack_id
                         left join vmc_adjust_stock_line as c on c.vmc_adjust_stock_id=a.id
                         where a.vmc_swap_id = {} and a.swap_product_id is not null and b.vmc_machine_id = {}
                         """.format(swap_order_id, machine_id)
                request.cr.execute(sql)
                swaped_product_list = request.cr.dictfetchall()
            else:
                swaped_product_list = []
            for line in info_list:
                stack_no = line['stack_no']
                box_no = line['box_no']
                product_id =False
                supply_type='补货'
                for product in swaped_product_list:
                    if stack_no in product['stack_no'] and box_no in product['box_no']:
                        if product['origin_product_id'] is not None:
                            supply_type = '换货'
                        else:
                            supply_type = '新增'
                        product_id = product['swap_product_id']
                        break
                    else:
                        if product['type'] == 'remove':
                            supply_type = '移除'
                        else:
                            supply_type = '补货'
                product_id =product_id or line['id']
                product = request.env['product.product'].sudo().browse(product_id)
                # 商品类别,饮品还是食品
                product_type = product.product_tmpl_id.categ_id.name or ""
                # 净含量
                net_weight = product.product_tmpl_id.net_weight or ""
                host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                host_url_replaced = host_url_replaced.replace(':88', '')
                image = host_url_replaced + image_url(product.product_tmpl_id, 'image', '300x300')
                product_details_image_url = self._get_product_details_image_url(product)
                records.append({'stack': {'box_no': line['box_no'], 'stack_no': line['stack_no']},
                                'product': {'seq_no': line['seq_no'], 'id': line['id'], 'stock': line['stock'],
                                            'stock_supplied': line['stock_supplied'], 'supply_type': supply_type,
                                            'price': line['price'], 'name': line['name'], 'image_url': image,
                                            'product_type': product_type, 'net_weight': net_weight,
                                            'product_details_image_url': product_details_image_url}
                                })
            res['records'] = records
        except Exception, e:
            res = {"error": "货道配置同步接口异常" + str(e)}
        _logger.debug("END vmc_stack_sync ...")
        return res

    # 商品详情图片链接
    def _get_product_details_image_url(self, product):
        # 数据库得到的字段, 包含 商品详情图片url 的html字符串
        product_details_html_info = product.product_tmpl_id.product_describe
        # 对字符串切片, 拿到图片的url
        if product_details_html_info:
            index_start = product_details_html_info.find('/') + 1  # 从斜杠之后开始切片
            index_end = product_details_html_info.find('.jpg')
            offset = len('.jpg')
            if index_end <> -1:
                product_details_image_url = product_details_html_info[index_start:index_end + offset]
            else:
                # product_details_image_url = product_details_html_info[index_start:]
                image_list = product_details_html_info.split('"')
                if image_list and len(image_list) > 1 and image_list[1] != None and len(image_list[1]) > 1:
                    product_details_image_url = image_list[1][1:]
                else:
                    product_details_image_url = ''
            host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
            host_url_replaced = host_url_replaced.replace(':88', '')
            product_details_image_url = (
                host_url_replaced + product_details_image_url)
        else:
            product_details_image_url = ''
        return product_details_image_url

    # 售货机同步完成接口
    @predispatch
    def vmc_sync_complete(self, *args, **kwargs):
        _logger.debug("Begin vmc_sync_complete ...")
        try:
            if not kwargs.get('sync_id'):
                return {'error': '缺少补货单ID'}
            sync_id = int(kwargs.get('sync_id'))
            sql = """select count(*) from vmc_adjust_stock  where id=%s and state='finished' and coalesce(machine_sync,'')<>'已同步' """ % sync_id
            request.cr.execute(sql)
            fet = request.cr.fetchone()
            if fet[0] == 0:
                return {'error': '补货单ID:%s 没有对应未同步的补货单' % sync_id}
            sql = """update vmc_adjust_stock set machine_sync='已同步' where id=%s""" % sync_id
            request.cr.execute(sql)

        except Exception, e:
            res = {"error": "售货机同步完成接口" + str(e)}
            return res
        _logger.debug("END vmc_sync_complete ...")
        return {'msg': 'success'}

    # 创建提货记录接口，接收补货单id列表，返回补货单列表中所有商品分类汇总后的数量以及商品的其他信息
    @predispatch
    def vmc_delivery_create(self, *args, **kwargs):
        _logger.debug("Begin vmc_delivery_create ...")

        supplys = kwargs.get("supplys")
        uid = request.env.uid
        # 补货单id列表默认有0,解决只有1张补货单的时候sql报错。
        supply_ids = [0]
        if supplys:
            for m in supplys:
                supply_ids.append(m["id"])
        status = kwargs['status']
        current_write_time=datetime.utcnow()
        if status == 1:
            # 更新补货单状态为提货中
            request.cr.execute("""
                    update vmc_adjust_stock set state='on_taking_delivery',write_date='%s' where id in %s
                """ % (current_write_time,tuple(supply_ids)))
        # 获取商品信息汇总
        result = self._get_delivery_product(supply_ids)
        # 获取主仓库的库存库位，车仓库的库存库位，主仓库的内部类型
        sql = """with a as (
                               select id,lot_stock_id,int_type_id,company_id,now()-interval'8 hours' as create_date from stock_warehouse where stock_type='common'  and
                               company_id=(select company_id from res_users where id=%s) order by id limit 1),
                         b as(
                              select id,lot_stock_id from stock_warehouse where stock_type='car'  and
                              company_id=(select company_id from res_users where id=%s) order by id limit 1)
                              select a.id,b.id,a.lot_stock_id,b.lot_stock_id,a.int_type_id,company_id,create_date from a
                              left join b on 1=1""" % (uid, uid)
        request.cr.execute(sql)
        fet = request.cr.fetchone()
        if not fet:
            return {'error': 'error:补货员（%s） 的一般仓库,车库未配置' % uid}
        common_stock_warehouse_id, car_stock_warehouse_id, common_stock_loctaion_id, car_stock_loctaion_id, int_type_id, company_id, create_date = fet
        if not common_stock_warehouse_id:
            return {'error': 'error:补货员（%s） 的一般仓库未配置' % uid}
        if not car_stock_warehouse_id:
            return {'error': 'error:补货员（%s） 的车库未配置' % uid}
        warehouse_obj = request.env['stock.warehouse'].sudo().browse(common_stock_warehouse_id)
        address = ''
        if warehouse_obj.partner_id:
            state_name = warehouse_obj.partner_id.state_id and warehouse_obj.partner_id.state_id.name or ''
            city_name = warehouse_obj.partner_id.city_id and warehouse_obj.partner_id.city_id.name or ''
            county = warehouse_obj.partner_id.county and warehouse_obj.partner_id.county.name or ''
            street2 = warehouse_obj.partner_id.street2 or ''
            street = warehouse_obj.partner_id.street or ''
            if state_name == city_name:
                state_name = ''
            address = '%s %s %s %s %s' % (state_name, city_name, county, street2, street)
        if result['records'] is not None:
            result['address'] = address
        _logger.debug("END vmc_delivery_create ...")
        return result

    # 获取补货单id列表获取提货商品分类汇总
    def _get_delivery_product(self, supply_ids):
        request.cr.execute("""
                      select g.name as name,m.name as unit, d.adjust_product_id as id,sum(d.replenish_demand) as quantity
                        from vmc_adjust_stock a
                        inner join vmc_adjust_stock_line d on d.vmc_adjust_stock_id=a.id
                        left join vmc_machine_stacks e on e.id=d.vmc_machine_stack_id
                        inner join product_product f on f.id=d.adjust_product_id
                        left join product_template g on g.id=f.product_tmpl_id
                        left join product_uom m on g.uom_id=m.id
                        where a.id in %s group by d.adjust_product_id,g.name,unit
                """ % (tuple(supply_ids),)
                           )
        delivery_info = request.cr.dictfetchall()
        total = len(delivery_info)
        if delivery_info:
            for i in range(0, total):
                product_obj = request.env['product.product'].sudo().browse(
                    delivery_info[i]["id"])
                host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                host_url_replaced = host_url_replaced.replace(':88', '')
                delivery_info[i]["image_url"] = host_url_replaced + image_url(
                    product_obj.product_tmpl_id, 'image', '300x300')
                delivery_info[i]["flavor"] = self._get_product_flavor(delivery_info[i]["id"])
        return {"records": delivery_info, "total": total}

    # 获取提款金额接口，返回售货机器表中现金余额+补得金额-售货机硬币盒剩余金额
    @predispatch
    def vmc_financial_take(self, *args, **kwargs):
        _logger.debug("Begin vmc_financial_take ...")
        machine_id = kwargs.get("machine_id")
        request.cr.execute("""
                        select coalesce(total_crash,0) as cash_in_vmc_plus_coin_supplied
                        from vmc_machines
                        where id= %s
                    """, (machine_id,))
        result = request.cr.dictfetchone()
        cash_in_vmc_plus_coin_supplied = result["cash_in_vmc_plus_coin_supplied"]
        request.cr.execute("""
                select coalesce(machine_coin_50cent,0) as fifty_cent_coin_count_in_coinbox, coalesce(machine_coin_100cent,0) as hundred_cent_coin_count_in_coinbox
                from vmc_money_record
                where vmc_machines_id= %s and type = 'giveby_supply'
                order by create_date desc
                limit 1
            """, (machine_id,))
        r = request.cr.dictfetchone()
        fifty_cent_coin_count_in_coinbox = r["fifty_cent_coin_count_in_coinbox"]
        hundred_cent_coin_count_in_coinbox = r["hundred_cent_coin_count_in_coinbox"]
        money_taken = cash_in_vmc_plus_coin_supplied - 0.5 * fifty_cent_coin_count_in_coinbox - 1 * hundred_cent_coin_count_in_coinbox
        _logger.debug("End vmc_financial_take ...")
        return {"machine_coin": money_taken}

    # 补硬币完成接口
    @predispatch
    def vmc_financial_supply(self, *args, **kwargs):
        _logger.debug("Begin vmc_financial_supply ...")
        machine_id = kwargs.get("machine_id")
        supply_id = str(kwargs.get("supply_id"))  # 补货单id
        uid = kwargs.get("uid")
        # 用自带硬币补得5角,1元硬币个数
        portable_coin_50cent = kwargs.get("portable_coin_50cent")
        portable_coin_100cent = kwargs.get("portable_coin_100cent")
        amount = portable_coin_100cent + 0.5 * portable_coin_50cent  # 补款额
        # 机器硬币盒剩余5角,1元硬币个数
        machine_coin_50cent = kwargs.get("machine_coin_50cent")
        machine_coin_100cent = kwargs.get("machine_coin_100cent")
        machine_coin_available = None  # 调用的方法所必需的参数
        request.cr.execute("""
                                select id, amount
                                from vmc_money_record
                                where adjust_stock_id = %s and type = 'giveby_supply'
                               """, (supply_id,)
                           )
        r = request.cr.dictfetchone()
        if r is None:  # 补货单补款记录不存在
            self._create_money_record(machine_id, uid, 'giveby_supply', amount, portable_coin_50cent,
                                      portable_coin_100cent, machine_coin_50cent, machine_coin_100cent, 'addin',
                                      supply_id, machine_coin_available)
            request.cr.execute("""
                                update vmc_machines set total_crash=total_crash+%s where id=%s
                            """, (amount, machine_id))
        else:  # 补货单补款记录已存在
            amount_supplied_last_time = r['amount']  # 修改前的补硬币记录
            self._update_money_record(amount, portable_coin_50cent, portable_coin_100cent, machine_coin_50cent,
                                      machine_coin_100cent, supply_id)
            request.cr.execute("""
                    update vmc_machines set total_crash=total_crash+%s-%s where id=%s
                """, (amount, amount_supplied_last_time, machine_id))
        _logger.debug("End vmc_financial_supply ...")
        # 刚刚插入数据库的补款记录
        request.cr.execute("""
                                select id
                                from vmc_money_record
                                where vmc_machines_id = %s
                                order by create_date desc
                                limit 1""", (machine_id,))
        r = request.cr.dictfetchall()
        records = [s['id'] for s in r]
        return {"records": records}

    # 更新补硬币记录
    def _update_money_record(self, amount, portable_coin_50cent, portable_coin_100cent, machine_coin_50cent,
                             machine_coin_100cent, supply_id):
        request.cr.execute(
            """
                update vmc_money_record set
                    amount = %s,
                    portable_coin_50cent = %s,
                    portable_coin_100cent = %s,
                    machine_coin_50cent = %s,
                    machine_coin_100cent = %s
                where adjust_stock_id = %s and type = 'giveby_supply'
            """,
            (
                amount, portable_coin_50cent, portable_coin_100cent, machine_coin_50cent, machine_coin_100cent,
                supply_id
            )
        )
        return {"msg": "success"}

    # 提款确认接口
    @predispatch
    def vmc_financial_take_confirm(self, *args, **kwargs):
        _logger.debug("Begin  vmc_financial_take_confirm ...")
        machine_id = kwargs.get("machine_id")
        uid = kwargs.get("uid") or request.env.uid
        machine_coin = kwargs.get("machine_coin")
        machine_coin_available = kwargs.get("machine_coin_available")
        adjust_stock_id = str(kwargs.get("supply_id"))  # 补货单id
        if machine_coin:
            # 调用的方法所必需的参数
            portable_coin_50cent, portable_coin_100cent, machine_coin_50cent, machine_coin_100cent = None, None, None, None
            self._create_money_record(machine_id, uid, 'get', machine_coin, portable_coin_50cent,
                                      portable_coin_100cent,
                                      machine_coin_50cent, machine_coin_100cent, 'takeout', adjust_stock_id,
                                      machine_coin_available)
            request.cr.execute("""
                        update vmc_machines set total_crash=total_crash-%s where id=%s
                    """, (machine_coin, machine_id))
            request.cr.execute("""
                                    update vmc_adjust_stock
                                    set is_withdrawn = True
                                    where id = %s
                                   """, (adjust_stock_id,)
                               )
        _logger.debug("End vmc_financial_take_confirm ...")
        return {"msg": "success"}

    # 创建取补款记录
    # 创建数据库补硬币记录
    def _create_money_record(self, machine_id, uid, record_type, amount, portable_coin_50cent,
                             portable_coin_100cent,
                             machine_coin_50cent, machine_coin_100cent, description, adjust_stock_id,
                             machine_coin_available):
        create_date = datetime.strftime(datetime.now(), '%Y-%m-%d %H:%M:%S')
        vmc_machines_id = machine_id
        create_uid = uid
        record_type = record_type
        amount = amount
        description = description
        request.cr.execute("""
                select b.name as machine_name,b.street2,b.street,j.name as country,k.name as city,l.name as county from vmc_machines a
                    left join res_partner b on b.id=a.partner_id
                    left join res_country_state j on j.id=b.state_id
                    left join res_country_state_city k on k.id=b.city_id
                    left join res_country_state_city_county l on l.id= b.county
                    where a.id=%s
            """, (machine_id,))
        machine_info = request.cr.dictfetchall()
        if machine_info:
            area = (machine_info[0]["country"] or "") + (machine_info[0]["city"] or "") + (
            machine_info[0]["county"] or "")
            address = machine_info[0]["street2"]
            location = machine_info[0]["street"]
        request.cr.execute("""
                insert into vmc_money_record
                (create_uid,create_date,vmc_machines_id,type,area,address,location,amount,
                portable_coin_50cent, portable_coin_100cent, machine_coin_50cent, machine_coin_100cent,
                description,state, adjust_stock_id, mention_amount)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
        create_uid, create_date, vmc_machines_id, record_type, area or "", address or "", location or "", amount,
        portable_coin_50cent, portable_coin_100cent, machine_coin_50cent, machine_coin_100cent,
        description or '', 'draft', adjust_stock_id or '', machine_coin_available))
        return {"msg": "success"}

    # 确认提货记录
    @predispatch
    def vmc_delivery_complete(self, *args, **kwargs):
        _logger.debug("Begin vmc_delivery_complete ...")
        try:
            uid = int(kwargs['uid']) or request.session.uid or SUPERUSER_ID
            name = request.env['res.users'].browse(uid).name
            move_obj = request.env['stock.move'].sudo()
            picking_obj = request.env['stock.picking'].sudo()
            if not kwargs.get('products'):
                return {'error': '缺少商品信息products'}
            if not kwargs.get('supplys'):
                return {'error': 'supplys'}
            supply_ids = [x['id'] for x in kwargs.get('supplys')]

            # 获取主仓库的库存库位，车仓库的库存库位，主仓库的内部类型
            sql = """with a as (
                                  select id,lot_stock_id,int_type_id,company_id,now()-interval'8 hours' as create_date from stock_warehouse where id=(select storehouse from vmc_adjust_stock where id=%s) order by id limit 1),
                            b as(
                                 select id,lot_stock_id from stock_warehouse where stock_type='car'  and
                                 company_id=(select company_id from res_users where id=%s) order by id limit 1)
                                 select a.id,b.id,a.lot_stock_id,b.lot_stock_id,a.int_type_id,company_id,create_date from a
                                 left join b on 1=1""" % (supply_ids[0], uid)

            request.cr.execute(sql)
            fet = request.cr.fetchone()
            if not fet:
                return {'error': 'error:补货单（%s） 的一般仓库,车库未配置' % supply_ids[0]}
            common_stock_warehouse_id, car_stock_warehouse_id, common_stock_loctaion_id, car_stock_loctaion_id, int_type_id, company_id, create_date = fet
            if not common_stock_warehouse_id:
                return {'error': 'error:补货单（%s） 的一般仓库未配置' % supply_ids[0]}
            if not car_stock_warehouse_id:
                return {'error': 'error:补货单（%s） 的车库未配置' % supply_ids[0]}

            sql = """select cast(array_agg(adjust_stock_code) as varchar(200)) from vmc_adjust_stock where id in %s and state in ('confirm','on_taking_delivery')""" % (
                tuple(supply_ids + [0, -1]),)
            request.cr.execute(sql)
            fet = request.cr.fetchone()
            code = fet and fet[0] or ''
            group_id1 = 0
            move_ids = []
            picking_ids = []
            for product_line in kwargs.get('products'):
                if not group_id1:
                    group_id1 = request.env['procurement.group'].create({'name': code + '提货'}).id
                product_id = product_line['id']
                quantity = product_line['quantity']
                uom_id = request.env['product.product'].browse(product_id).uom_id.id
                # 主库－－车库
                move_vals = {
                    'name': code + ':提货',
                    'company_id': company_id,
                    'product_id': product_id,
                    'product_uom': uom_id,
                    'product_uom_qty': quantity,
                    'location_id': common_stock_loctaion_id,
                    'location_dest_id': car_stock_loctaion_id,
                    'procure_method': 'make_to_stock',
                    'origin': code + ':提货',
                    'picking_type_id': int_type_id,
                    'date': create_date,
                    'date_expected': create_date,
                    'group_id': group_id1,
                    'warehouse_id': common_stock_warehouse_id,
                    'svm_type': '提货',
                    'supply_id': uid
                }
                # picking_vals = {
                #     'name': code + ':提货',
                #     'company_id': company_id,
                #     'product_id': product_id,
                #     'product_uom': uom_id,
                #     'product_uom_qty': quantity,
                #     'location_id': common_stock_loctaion_id,
                #     'location_dest_id': car_stock_loctaion_id,
                #     'procure_method': 'make_to_stock',
                #     'origin': code + ':提货',
                #     'picking_type_id': int_type_id,
                #     'date': create_date,
                #     'date_expected': create_date,
                #     'group_id': group_id1,
                #     'warehouse_id': common_stock_warehouse_id,
                #     'svm_type': '提货',
                #     'supply_id': uid
                # }
                move_ids += [move_obj.create(move_vals).id]
                # picking_ids += [picking_obj.create(request.cr, SUPERUSER_ID, picking_vals)]
            current_write_time=datetime.utcnow()
            if move_ids:
                move_obj.browse(move_ids).action_confirm()
                move_obj.browse(move_ids).force_assign()
                move_obj.browse(move_ids).action_done()
                sql = """update stock_pack_operation set qty_done=product_qty where picking_id in
                                   ( select distinct picking_id  as picking_id  from stock_move where id in %s);
                                   ----picking与补货单建立联系
                                   insert into adjust_move_rel (vmc_adjust_stock_id,stock_move_id)
                                   select b.id,a.id  as move_id  from stock_move a
                                   left join vmc_adjust_stock b on 1=1
                                   where b.id in %s and a.id in %s;
                                   update vmc_adjust_stock set state='after_taked_delivery',write_date='%s' where id in %s;
                                   update stock_picking set svm_type='提货',supply_id=%s
                                   where id in ( select distinct picking_id  as picking_id  from stock_move where id in %s);""" % (
                    tuple(move_ids + [0, -1]), tuple(supply_ids + [0, -1]), tuple(move_ids + [0, -1]),current_write_time,
                    tuple(supply_ids + [0, -1]), uid, tuple(move_ids + [0, -1]),)
                request.cr.execute(sql)
                # if picking_ids:
                #     picking_obj.action_confirm(request.cr, SUPERUSER_ID, picking_ids)
                #     picking_obj.force_assign(request.cr, SUPERUSER_ID, picking_ids)
                #     picking_obj.action_done(request.cr, SUPERUSER_ID, picking_ids)

        except Exception, e:
            _logger.error(e)
            request.cr.rollback()
            res = {"error": "确认提货记录:" + str(e)}
            return res
        _logger.debug("END vmc_delivery_complete ...")
        return {'msg': 'success'}

    # 还货商品列表接口
    @predispatch
    def vmc_supply_refund_list(self, *args, **kwargs):
        _logger.debug("Begin vmc_supply_refund_list ...")
        try:
            if not kwargs.get('uid'):
                return {'error': '缺少补货员ID'}
            uid = int(kwargs.get('uid'))
            request.cr.execute("""select adjust_stock_code from vmc_adjust_stock where supply_operator=%s and state='reslenishment' """,(uid,))
            rfet = request.cr.dictfetchall()
            if rfet:
                return {
                    'records': [],
                    'total': 0,
                    'msg': '后台处理中，请稍后手动刷新',
                }
            sql = """select date from stock_move where supply_id=%s and svm_type = '还货' order by date desc limit 1""" % (
            uid)
            request.cr.execute(sql)
            fet = request.cr.fetchone()
            date_sql = fet and """ date > '%s'""" % (fet[0]) or ' 1=1'
            sql = """with a as (
                            select product_id,product_uom,-sum(product_uom_qty) as qty from stock_move
                            where  svm_type in ('补货') and %s and supply_id=%s
                            group by  product_id,product_uom
                            union all
                            select product_id,product_uom,sum(product_uom_qty) from stock_move
                            where  svm_type in ('清空','提货') and %s and supply_id=%s
                            group by  product_id,product_uom
                            )
                        select a.product_id,b.name as uom,sum(a.qty) as qty,d.name from a
                        left join product_uom b on b.id=a.product_uom
                        left join product_product c on c.id=a.product_id
                        left join product_template d on d.id=c.product_tmpl_id
                        group by product_id,b.id,d.name """ % (date_sql, uid, date_sql, uid)
            request.cr.execute(sql)
            fet = request.cr.fetchall()
            result = {'total': len(fet), 'msg': ''}
            records = []
            for line in fet:
                product_id, unit, quantity, name = line
                if quantity == 0:
                    continue
                flavor = self._get_product_flavor(product_id)
                product_obj = request.env['product.product'].sudo().browse(product_id)
                host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                host_url_replaced = host_url_replaced.replace(':88', '')
                url = host_url_replaced + image_url(product_obj.product_tmpl_id, 'image', '300x300')
                records.append(
                    {'id': product_id, 'unit': unit, 'name': name, 'image_url': url, 'flavor': flavor or '原味',
                     'quantity': quantity})
            result['records'] = records
            # 获取主仓库的库存库位，车仓库的库存库位，主仓库的内部类型
            sql = """with a as (
                                               select id,lot_stock_id,int_type_id,company_id,now()-interval'8 hours' as create_date from stock_warehouse where stock_type='common'  and
                                               company_id=(select company_id from res_users where id=%s) order by id limit 1),
                                         b as(
                                              select id,lot_stock_id from stock_warehouse where stock_type='car'  and
                                              company_id=(select company_id from res_users where id=%s) order by id limit 1)
                                              select a.id,b.id,a.lot_stock_id,b.lot_stock_id,a.int_type_id,company_id,create_date from a
                                              left join b on 1=1""" % (uid, uid)
            request.cr.execute(sql)
            fet = request.cr.fetchone()
            if not fet:
                return {'error': 'error:补货员（%s） 的一般仓库,车库未配置' % uid}
            common_stock_warehouse_id, car_stock_warehouse_id, common_stock_loctaion_id, car_stock_loctaion_id, int_type_id, company_id, create_date = fet
            if not common_stock_warehouse_id:
                return {'error': 'error:补货员（%s） 的一般仓库未配置' % uid}
            if not car_stock_warehouse_id:
                return {'error': 'error:补货员（%s） 的车库未配置' % uid}
            warehouse_obj = request.env['stock.warehouse'].sudo().browse(common_stock_warehouse_id)
            address = ''
            if warehouse_obj.partner_id:
                state_name = warehouse_obj.partner_id.state_id and warehouse_obj.partner_id.state_id.name or ''
                city_name = warehouse_obj.partner_id.city_id and warehouse_obj.partner_id.city_id.name or ''
                county = warehouse_obj.partner_id.county and warehouse_obj.partner_id.county.name or ''
                street2 = warehouse_obj.partner_id.street2 or ''
                street = warehouse_obj.partner_id.street or ''
                if state_name == city_name:
                    state_name = ''
                address = '%s %s %s %s %s' % (state_name, city_name, county, street2, street)
            if result['records'] is not None:
                result['address'] = address
        except Exception, e:
            request.cr.rollback()
            res = {"error": "还货商品列表接口" + str(e)}
            return res
        _logger.debug("END vmc_supply_refund_list ...")
        return result

    # 还货完成接口:  车库－－》主库位
    @predispatch
    def vmc_supply_refund_complete(self, *args, **kwargs):
        _logger.debug("Begin vmc_supply_refund_complete ...")
        try:
            uid = request.env.uid
            name = request.env['res.users'].browse([uid]).name
            if not kwargs.get('records') or len(kwargs.get('records')) == 0:
                return {'error': '没有找到records' % uid}

            move_obj = request.env['stock.move'].sudo()
            picking_obj = request.env['stock.picking']

            # 获取主仓库的库存库位，车仓库的库存库位，主仓库的内部类型
            sql = """with a as (
                                select id,lot_stock_id,int_type_id,company_id,now()-interval'8 hours' as create_date from stock_warehouse where stock_type='common'  and
                                company_id=(select company_id from res_users where id=%s) order by id limit 1),
                          b as(
                               select id,lot_stock_id from stock_warehouse where stock_type='car'  and
                               company_id=(select company_id from res_users where id=%s) order by id limit 1)
                               select a.id,b.id,a.lot_stock_id,b.lot_stock_id,a.int_type_id,company_id,create_date from a
                               left join b on 1=1""" % (uid, uid)
            request.cr.execute(sql)
            fet = request.cr.fetchone()
            if not fet:
                return {'error': 'error:补货员（%s） 的一般仓库,车库未配置' % name}
            common_stock_warehouse_id, car_stock_warehouse_id, common_stock_loctaion_id, car_stock_loctaion_id, int_type_id, company_id, create_date = fet
            if not common_stock_warehouse_id:
                return {'error': 'error:补货员（%s） 的一般仓库未配置' % name}
            if not car_stock_warehouse_id:
                return {'error': 'error:补货员（%s） 的车库未配置' % name}
            name = request.env['res.users'].browse(uid).name
            code = '还货:补货员%s' % name
            group_id1 = 0
            move_ids = []
            note = kwargs.get('note', '')
            for line in kwargs.get('records'):
                product_id, qty = line['id'], line['quantity']
                if not group_id1:
                    group_id1 = request.env['procurement.group'].create({'name': code}).id
                uom_id = request.env['product.product'].browse(product_id).uom_id.id
                # 主库－－车库
                move_vals = {
                    'name': code,
                    'company_id': company_id,
                    'product_id': product_id,
                    'product_uom': uom_id,
                    'product_uom_qty': qty,
                    'location_dest_id': common_stock_loctaion_id,
                    'location_id': car_stock_loctaion_id,
                    'procure_method': 'make_to_stock',
                    'origin': code,
                    'picking_type_id': int_type_id,
                    'date': create_date,
                    'date_expected': create_date,
                    'group_id': group_id1,
                    'warehouse_id': common_stock_warehouse_id,
                    'svm_type': '还货',
                    'supply_id': uid,
                    'note': note
                }
                move_ids += [move_obj.create(move_vals).id]
            if move_ids:
                move_obj.browse(move_ids).action_confirm()
                move_obj.browse(move_ids).force_assign()
                move_obj.browse(move_ids).action_done()
                sql = """update stock_pack_operation set qty_done=product_qty where picking_id in
                          ( select distinct picking_id  as picking_id  from stock_move where id in %s);
                          update stock_picking set svm_type='还货',supply_id=%s, note = '%s'
                              where id in ( select distinct picking_id  as picking_id  from stock_move where id in %s);""" % (
                    tuple(move_ids + [0, -1]), uid, note, tuple(move_ids + [0, -1]),)
                request.cr.execute(sql)
        except Exception, e:
            request.cr.rollback()
            res = {"error": "还货完成接口" + str(e)}
            return res
        _logger.debug("End vmc_supply_refund_complete ...")
        return {"msg": "success", 'move_ids': move_ids}

    # 提货历史时间列表接口
    @predispatch
    def vmc_delivery_history_list(self, *args, **kwargs):
        _logger.debug("Begin  vmc_delivery_history_list ...")
        uid = kwargs.get('uid') or request.env.uid or SUPERUSER_ID
        days_backwards = 30
        utctime_start_lastmonth = utctime_start_lastdays(days_backwards)
        sql = """select a.id as stock_picking_id, to_char(a.create_date+interval'8 hour', 'yyyy-MM-dd hh24:MI:ss') as date_time
                 from stock_picking a
                 where a.svm_type in ('提货') and a.supply_id = {} and a.create_date >= '{}'
                 order by a.create_date desc
               """.format(uid, datetime.strptime(utctime_start_lastmonth, '%Y-%m-%d %H:%M:%S'))
        request.cr.execute(sql)
        delivery_history_list = request.cr.dictfetchall()
        cn_date_today = cn_current_year_and_date()
        cn_date_yesterday = cn_yesterday_year_and_date()
        for delivery_history in delivery_history_list:
            cn_date = delivery_history['date_time'].split()[0]
            if cn_date == cn_date_today:
                delivery_history['date_special'] = '今天'
            elif cn_date == cn_date_yesterday:
                delivery_history['date_special'] = '昨天'
            else:
                delivery_history['date_special'] = ''
        result = {}
        result['records'] = delivery_history_list
        _logger.debug("End vmc_delivery_history_list ...")
        return result

    def _delivery_or_refund_address(self, uid):
        # 获取主仓库的库存库位，车仓库的库存库位，主仓库的内部类型
        sql = """with a as (
                           select id,lot_stock_id,int_type_id,company_id,now()-interval'8 hours' as create_date from stock_warehouse where stock_type='common'  and
                           company_id=(select company_id from res_users where id=%s) order by id limit 1),
                     b as(
                          select id,lot_stock_id from stock_warehouse where stock_type='car'  and
                          company_id=(select company_id from res_users where id=%s) order by id limit 1)
                          select a.id,b.id,a.lot_stock_id,b.lot_stock_id,a.int_type_id,company_id,create_date from a
                          left join b on 1=1""" % (uid, uid)
        request.cr.execute(sql)
        fet = request.cr.fetchone()
        if not fet:
            return {'error': 'error:补货员（%s） 的一般仓库,车库未配置' % uid}
        common_stock_warehouse_id, car_stock_warehouse_id, common_stock_loctaion_id, car_stock_loctaion_id, int_type_id, company_id, create_date = fet
        if not common_stock_warehouse_id:
            return {'error': 'error:补货员（%s） 的一般仓库未配置' % uid}
        if not car_stock_warehouse_id:
            return {'error': 'error:补货员（%s） 的车库未配置' % uid}
        warehouse_obj = request.env['stock.warehouse'].sudo().browse(common_stock_warehouse_id)
        address = ''
        if warehouse_obj.partner_id:
            state_name = warehouse_obj.partner_id.state_id and warehouse_obj.partner_id.state_id.name or ''
            city_name = warehouse_obj.partner_id.city_id and warehouse_obj.partner_id.city_id.name or ''
            county = warehouse_obj.partner_id.county and warehouse_obj.partner_id.county.name or ''
            street2 = warehouse_obj.partner_id.street2 or ''
            street = warehouse_obj.partner_id.street or ''
            if state_name == city_name:
                state_name = ''
            address = '%s %s %s %s %s' % (state_name, city_name, county, street2, street)
        return address

    # 提货历史列表(某单提货单提货商品详情)接口
    @predispatch
    def vmc_history_delivery(self, *args, **kwargs):
        _logger.debug("Begin  vmc_history_delivery ...")
        uid = kwargs.get('uid') or request.env.uid or SUPERUSER_ID
        if 'stock_picking_id' not in kwargs:
            # 今天
            sql = """select a.product_id,b.name,sum(product_uom_qty),d.name from stock_move a
                     left join product_uom b on b.id=a.product_uom
                     left join product_product c on c.id=a.product_id
                     left join product_template d on d.id=c.product_tmpl_id
                     where a.svm_type in ('提货')  and a.supply_id=%s and a.date>=current_date-interval'8 hour' and a.date<current_date+interval'16 hour'
                     group by a.product_id,b.name,d.name """ % uid
            request.cr.execute(sql)
            fet = request.cr.fetchall()
            result = {'total': len(fet)}
            records = []
            for line in fet:
                product_id, unit, quantity, name = line
                if quantity == 0:
                    continue
                flavor = self._get_product_flavor(product_id)
                product_obj = request.env['product.product'].sudo().browse(product_id)
                host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                host_url_replaced = host_url_replaced.replace(':88', '')
                url = host_url_replaced + image_url(product_obj.product_tmpl_id, 'image',
                                                                                 '300x300')
                records.append(
                    {'id': product_id, 'unit': unit, 'name': name, 'image_url': url, 'flavor': flavor or '原味',
                     'quantity': quantity})
            _logger.debug("End vmc_history_delivery ...")
            result['records'] = records
            return result
        else:
            stock_picking_id = kwargs['stock_picking_id']
            result = self._single_vmc_delivery_history(uid, stock_picking_id)
            # 提货时间
            sql = """
                        select to_char(sp.create_date+interval'8 hour', 'yyyy-MM-dd hh24:MI:ss') as date_time
                        from stock_picking as sp
                        where id = {}
                        """.format(stock_picking_id)
            request.cr.execute(sql)
            date_time_dict = request.cr.dictfetchone()
            result['date_time'] = date_time_dict['date_time']
            address = self._delivery_or_refund_address(uid)
            result['address'] = address
            return result

    def _single_vmc_delivery_history(self, uid, stock_picking_id):
        sql = """select a.product_id,b.name,sum(product_uom_qty),d.name
                 from stock_picking sp
                 left join stock_move a on sp.id = a.picking_id
                 left join product_uom b on b.id=a.product_uom
                 left join product_product c on c.id=a.product_id
                 left join product_template d on d.id=c.product_tmpl_id
                 where sp.id = {} and a.svm_type in ('提货') and a.supply_id={}
                 group by  a.product_id,b.name,d.name """.format(stock_picking_id, uid)
        request.cr.execute(sql)
        fet = request.cr.fetchall()
        result = {'total': len(fet)}
        records = []
        for line in fet:
            product_id, unit, quantity, name = line
            if quantity == 0:
                continue
            flavor = self._get_product_flavor(product_id)
            product_obj = request.env['product.product'].sudo().browse(product_id)
            host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
            host_url_replaced = host_url_replaced.replace(':88', '')
            url = host_url_replaced + image_url(product_obj.product_tmpl_id, 'image',
                                                '300x300')
            records.append(
                {'id': product_id, 'unit': unit, 'name': name, 'image_url': url, 'flavor': flavor or '原味',
                 'quantity': quantity})
        _logger.debug("End vmc_history_delivery ...")
        result['records'] = records
        return result

    # 还货历史时间列表接口
    @predispatch
    def vmc_refund_history_list(self, *args, **kwargs):
        _logger.debug("Begin  vmc_refund_history_list ...")
        uid = kwargs.get('uid') or request.env.uid or SUPERUSER_ID
        days_backwards = 30
        utctime_start_lastmonth = utctime_start_lastdays(days_backwards)
        sql = """select a.id as stock_picking_id, to_char(a.create_date+interval'8 hour', 'yyyy-MM-dd hh24:MI:ss') as date_time
                 from stock_picking a
                 where a.svm_type in ('还货') and a.supply_id = {} and a.create_date >= '{}'
                 order by a.create_date desc
               """.format(uid, datetime.strptime(utctime_start_lastmonth, '%Y-%m-%d %H:%M:%S'))
        request.cr.execute(sql)
        refund_history_list = request.cr.dictfetchall()
        cn_date_today = cn_current_year_and_date()
        cn_date_yesterday = cn_yesterday_year_and_date()
        for refund_history in refund_history_list:
            cn_date = refund_history['date_time'].split()[0]
            if cn_date == cn_date_today:
                refund_history['date_special'] = '今天'
            elif cn_date == cn_date_yesterday:
                refund_history['date_special'] = '昨天'
            else:
                refund_history['date_special'] = ''
        result = {}
        result['records'] = refund_history_list
        _logger.debug("End vmc_refund_history_list ...")
        return result

    # 还货历史列表(某单还货单还货商品详情)接口
    @predispatch
    def vmc_history_refund(self, *args, **kwargs):
        _logger.debug("Begin  vmc_history_refund ...")
        uid = kwargs.get('uid') or request.env.uid or SUPERUSER_ID
        if 'stock_picking_id' not in kwargs:
            # 今天
            sql = """select a.product_id,b.name,sum(product_uom_qty),d.name from stock_move a
                     left join product_uom b on b.id=a.product_uom
                     left join product_product c on c.id=a.product_id
                     left join product_template d on d.id=c.product_tmpl_id
                     where  a.svm_type in ('还货')  and a.supply_id=%s and a.date>=current_date-interval'8 hour' and a.date<current_date+interval'16 hour'
                     group by  a.product_id,b.name,d.name """ % uid
            request.cr.execute(sql)
            fet = request.cr.fetchall()
            result = {'total': len(fet)}
            records = []
            for line in fet:
                product_id, unit, quantity, name = line
                if quantity == 0:
                    continue
                flavor = self._get_product_flavor(product_id)
                product_obj = request.env['product.product'].sudo().browse(product_id)
                host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
                host_url_replaced = host_url_replaced.replace(':88', '')
                url = host_url_replaced + image_url(product_obj.product_tmpl_id, 'image',
                                                                                 '300x300')
                records.append(
                    {'id': product_id, 'unit': unit, 'name': name, 'image_url': url, 'flavor': flavor or '原味',
                     'quantity': quantity})
            _logger.debug("End vmc_history_refund ...")
            result['records'] = records
            return result
        else:
            stock_picking_id = kwargs['stock_picking_id']
            result = self._single_vmc_refund_history(uid, stock_picking_id)
            # 还货时间, 备注
            sql = """
                select to_char(sp.create_date+interval'8 hour', 'yyyy-MM-dd hh24:MI:ss') as date_time, sp.note
                from stock_picking as sp
                where id = {}
                """.format(stock_picking_id)
            request.cr.execute(sql)
            date_time_and_note = request.cr.dictfetchone()
            date_time = date_time_and_note['date_time']
            result['date_time'] = date_time
            note = date_time_and_note['note']
            result['note'] = note or ''
            address = self._delivery_or_refund_address(uid)
            result['address'] = address
            return result

    def _single_vmc_refund_history(self, uid, stock_picking_id):
        sql = """select a.product_id,b.name,sum(product_uom_qty),d.name
                 from stock_picking sp
                 left join stock_move a on sp.id = a.picking_id
                 left join product_uom b on b.id=a.product_uom
                 left join product_product c on c.id=a.product_id
                 left join product_template d on d.id=c.product_tmpl_id
                 where sp.id = {} and a.svm_type in ('还货') and a.supply_id={}
                 group by a.product_id,b.name,d.name """.format(stock_picking_id, uid)
        request.cr.execute(sql)
        fet = request.cr.fetchall()
        result = {'total': len(fet)}
        records = []
        for line in fet:
            product_id, unit, quantity, name = line
            if quantity == 0:
                continue
            flavor = self._get_product_flavor(product_id)
            product_obj = request.env['product.product'].sudo().browse(product_id)
            host_url_replaced = request.httprequest.host_url.replace('http:', 'https:')
            host_url_replaced = host_url_replaced.replace(':88', '')
            url = host_url_replaced + image_url(product_obj.product_tmpl_id, 'image',
                                                '300x300')
            records.append(
                {'id': product_id, 'unit': unit, 'name': name, 'image_url': url, 'flavor': flavor or '原味',
                 'quantity': quantity})
        _logger.debug("End vmc_history_delivery ...")
        result['records'] = records
        return result

    # 补货历史列表接口
    @predispatch
    def vmc_history_supply(self, *args, **kwargs):
        _logger.debug("Begin  vmc_history_supply ...")
        resutlt = []
        uid = kwargs.get('uid') or request.env.uid or SUPERUSER_ID
        operator_id = uid
        app_version = kwargs.get('app_version', '')
        if 'time_interval' not in kwargs:  # 兼容老版本app
            result = self._vmc_history_supply_old_version(app_version, uid, operator_id)
            return result
        else:
            time_interval=kwargs.get('time_interval')
            machine_id = kwargs.get('machine_id')
            if time_interval=='day' and machine_id is None:
                # 获取今日补货单为完成状态的机器id,补货单id
                sql = """
                        select id,adjust_stock_code,vmc_machine_id
                        from vmc_adjust_stock a
                        where a.supply_operator=%s and state='finished'
                          and  a.supply_date>=current_date-interval'8 hour'
                          and a.supply_date<current_date+interval'16 hour'
                        order by a.supply_date desc
                      """ % (uid,)
                request.cr.execute(sql)
                fet = request.cr.dictfetchall()
                total = len(fet)
            elif time_interval == 'month':
                if machine_id is not None:
                    sql_vmc_filter = " and a.vmc_machine_id = {} order by a.supply_date desc".format(machine_id)
                else:
                    sql_vmc_filter = " order by a.supply_date desc"
                # utc当天0:00
                utc_today_zero=datetime.strptime(utc_strftime_zeropoint(), "%Y-%m-%d %H:%M:%S")
                # utc30天前的0:00
                utc_before_zero=datetime.strptime(utctime_start_lastdays(31), "%Y-%m-%d %H:%M:%S")
                sql="""
                        select id,adjust_stock_code,vmc_machine_id
                        from vmc_adjust_stock a
                        where a.supply_operator='%s' and state='finished'
                          and  a.supply_date>= '%s'
                          and a.supply_date< '%s'
                    """%(uid, utc_before_zero, utc_today_zero)
                sql += sql_vmc_filter
                request.cr.execute(sql)
                fet = request.cr.dictfetchall()
                total = len(fet)
            elif time_interval == 'day' and machine_id is not None:
                sql_vmc_filter = " and a.vmc_machine_id = {} order by a.supply_date desc".format(machine_id)
                # utc当天0:00
                utc_today_zero = datetime.strptime(utc_strftime_zeropoint(), "%Y-%m-%d %H:%M:%S")
                # utc30天前的0:00
                utc_before_zero = datetime.strptime(utctime_start_lastdays(31), "%Y-%m-%d %H:%M:%S")
                sql = """
                        select id,adjust_stock_code,vmc_machine_id
                        from vmc_adjust_stock a
                        where a.supply_operator='%s' and state='finished'
                          and  a.supply_date>= '%s'
                    """ % (uid, utc_before_zero)
                sql += sql_vmc_filter
                request.cr.execute(sql)
                fet = request.cr.dictfetchall()
                total = len(fet)
            if fet:
                # return {"message":"没有补货历史"}
                # machine_id_list=[0]
                for m in fet:
                    # machine_id_list.append(m["vmc_machine_id"])
                    request.cr.execute("""
                            select vmc_machines_id,sum(amount),description
                                from vmc_money_record b where b.create_uid=%s
                                and vmc_machines_id =%s and b.adjust_stock_id=%s
                                group by description,vmc_machines_id""", (uid, m["vmc_machine_id"], str(m["id"])))
                    money_info = request.cr.dictfetchall()
                    # 开始查询时间, 当日北京时间0点
                    time_start = utc_strftime_zeropoint()
                    sql_extra_day = " and a.adjust_stock_code= '{}' and a.supply_date >= '{}'".format(m["adjust_stock_code"], time_start)
                    if time_interval=='day' and machine_id is None:
                        result_item = self._getsupply_order(app_version, m["vmc_machine_id"], ['finished', '0'], operator_id, sql_extra_day)
                    elif time_interval=='month':
                        sql_extra_month = " and a.adjust_stock_code= '{}' and a.supply_date >= '{}'".format(m["adjust_stock_code"], utc_before_zero)
                        result_item = self._getsupply_order(app_version, m["vmc_machine_id"], ['finished', '0'], operator_id, sql_extra_month)
                    elif time_interval=='day' and machine_id is not None:
                        sql_extra_month = " and a.adjust_stock_code= '{}' and a.supply_date >= '{}'".format(m["adjust_stock_code"], utc_before_zero)
                        result_item = self._getsupply_order(app_version, m["vmc_machine_id"], ['finished', '0'], operator_id, sql_extra_month)
                    result_item["financial"] = {"take_amount": "", "supply_amount": ""}

                    for x in money_info:
                        if x["description"] == 'takeout':
                            result_item["financial"]["take_amount"] = x["sum"] or "0"
                        if x["description"] == "addin":
                            result_item["financial"]["supply_amount"] = x["sum"] or "0"

                    resutlt.append(result_item)

            _logger.debug("End vmc_history_supply ...")

            return {"records": resutlt, "total": total}

    def _vmc_history_supply_old_version(self, app_version, uid, operator_id):
        resutlt = []
        # 获取今日补货单为完成状态的机器id
        request.cr.execute("""
                        select distinct vmc_machine_id from vmc_adjust_stock a where a.supply_operator=%s and state='finished'
                            and  a.supply_date>=current_date-interval'8 hour'
                            and a.supply_date<current_date+interval'16 hour' """, (uid,))
        fet = request.cr.dictfetchall()
        total = len(fet)
        if fet:
            # return {"message":"没有补货历史"}
            # machine_id_list=[0]
            for m in fet:
                # machine_id_list.append(m["vmc_machine_id"])
                request.cr.execute("""
                                select vmc_machines_id,sum(amount),description
                                    from vmc_money_record b where b.create_uid=%s
                                    and b.create_date>=current_date-interval'8 hour' and b.create_date<current_date+interval'16 hour'
                                    and vmc_machines_id =%s
                                    group by description,vmc_machines_id""", (uid, m["vmc_machine_id"]))
                money_info = request.cr.dictfetchall()
                # 开始查询时间, 当日utc时间0点
                time_start = utc_strftime_zeropoint()
                sql_extra = " and a.supply_date >= '{}'".format(time_start)
                result_item = self._getsupply_order(app_version, m["vmc_machine_id"], ['finished', '0'], operator_id, sql_extra)

                result_item["financial"] = {"take_amount": "", "supply_amount": ""}
                for x in money_info:
                    if x["description"] == 'takeout':
                        result_item["financial"]["take_amount"] = x["sum"] or "0"
                    if x["description"] == "addin":
                        result_item["financial"]["supply_amount"] = x["sum"] or "0"

                resutlt.append(result_item)

        _logger.debug("End vmc_history_supply ...")

        return {"records": resutlt, "total": total}
    # 补货员今日任务接口
    @predispatch
    def vmc_stack_today(self, *args, **kwargs):
        _logger.debug("Begin  vmc_stack_today ...")

        uid = kwargs.get('uid') or request.env.uid or SUPERUSER_ID
        request.cr.execute("""
                select count(*) as amount,state from vmc_adjust_stock where supply_operator=%s
                    group by state""", (uid,))
        state_info = request.cr.dictfetchall()
        confirm_count = taking_count = taked_count = finished_count = 0
        to_supply = taked_count
        for x in state_info:
            if x["state"] == "confirm":
                confirm_count = x["amount"]
            elif x["state"] == "on_taking_delivery":
                taking_count = x["amount"]
            elif x["state"] == "after_taked_delivery":
                taked_count = x["amount"]

        request.cr.execute("""
                        select count(*) as amount from vmc_adjust_stock where supply_date >= current_date-interval'8 hour'
                            and supply_date < current_date+interval'16 hour' and state='finished' and is_withdrawn = False
                            and supply_operator=%s
                        """, (uid,))
        r = request.cr.dictfetchone()  # 今日已完成但未提款的补货单
        to_supply += r['amount']

        request.cr.execute("""
                select count(*) as amount,state from vmc_adjust_stock where supply_date >= current_date-interval'8 hour'
                    and supply_date < current_date+interval'16 hour' and state='finished' and supply_operator=%s
                    group by state""", (uid,))
        state_finished = request.cr.dictfetchall()
        if state_finished:
            for x in state_finished:
                if x["state"] == "finished":
                    finished_count = x["amount"]
        # 计算今日需还款
        request.cr.execute("""
                select sum(amount) as amount,type from vmc_money_record where create_uid=%s and state='draft'  group by type
            """, (uid,))
        money_record = request.cr.dictfetchall()
        giveby_supply_total = get_total = 0
        if money_record:
            for x in money_record:
                if x["type"] == "giveby_supply":
                    if x["amount"]:
                        giveby_supply_total = float(x["amount"])
                    else:
                        giveby_supply_total = giveby_supply = 0
                if x["type"] == "get":
                    if x["amount"]:
                        get_total = float(x["amount"])
                    else:
                        get_total = 0
        request.cr.execute("""
                select sum(pre_money) as pre_money_total from vmc_money where supply_operator=%s and state='confirm1'
            """, (uid,))
        pre_money_record = request.cr.dictfetchall()
        if pre_money_record:
            for x in pre_money_record:
                if x["pre_money_total"]:
                    pre_money_total = float(x["pre_money_total"])
                else:
                    pre_money_total = 0
        else:
            pre_money_total = 0

        # 财务收款，会把对应的补款记录改为confirm状态
        giveby_confirm=0
        request.cr.execute("""
                select sum(amount) as amount from vmc_money_record where create_uid=%s and state='confirm' and type='giveby_supply'
            """, (uid,))
        fet =request.cr.fetchone()
        if fet:
            if fet[0] != None:
                giveby_confirm=fet[0]
            else:
                giveby_confirm=0
        refund_money = (pre_money_total - giveby_supply_total-giveby_confirm) + get_total
        # 当确认硬币归还，和财务收款后，pre_money_total=0且get_total=0时，refund_money=-giveby_supply_total-giveby_confirm,此时应归还0
        if (refund_money+giveby_supply_total+giveby_confirm)==0:
            refund_money=0

        # if refund_money < 0:
        #     refund_money = 0
        # 没有预支硬币的情况为负数

        # 财务收款后，类型为取款（get）且状态为新建（draft）的取款记录状态变为 完成（done）,补款记录从draft改为confirm,则get_total=0
        # 确认硬币归还后，则已取硬币（confirm1）状态变为已还硬币，则pre_money_total=0
        if get_total == 0 and pre_money_total == 0:
            refund_state = 1
        else:
            refund_state = 0
        kwargs = {
            'uid': uid,
        }
        to_refund = self.vmc_supply_refund_list(**kwargs)
        to_refund = to_refund.get('total', 0)
        _logger.debug("End vmc_stack_today ...")
        return {"total_count": confirm_count + taking_count + taked_count + finished_count,
                "to_delivery": confirm_count + taking_count,
                "to_supply": to_supply,
                "to_refund": to_refund,
                "supplied_count": finished_count,
                "refund_money": refund_money,
                "refund_state": refund_state}

    # 删除数据库原补硬币记录
    @predispatch
    def del_vmc_money_record(self, *args, **kwargs):
        ids = tuple(kwargs.get('ids'))
        machine_id = kwargs.get('machine_id')
        portable_coin = kwargs.get('portable_coin')
        request.cr.execute(
            """
            delete from vmc_money_record
            where id in %s
            """, (ids,)
        )
        request.cr.execute("""
                            update vmc_machines set total_crash=total_crash - %s where id=%s
                        """, (portable_coin, machine_id))
        return {"success": "原补硬币记录删除成功"}

    # 获取指令下发接口 by dazhao
    @predispatch
    def vmc_instructions_issued(self, *args, **kwargs):
        _logger.debug("Begin  vmc_instructions_issued ...")

        machine_id = kwargs.get('machine_id')
        request.cr.execute(
            """
            select v.* from vmc_instructions_issued v,vmc_instruction_machines_relation  t
            where v."id"=t.industrial_pc_restart and t.machines_id=%s and v.instructions_state='confirm'
            order by v.important_level desc,v.id
            """, (machine_id,))
        instructions_list = request.cr.dictfetchall()

        if not instructions_list or len(instructions_list) == 0:
            return {
                "records": []
            }

        result_list = []
        for instructions in instructions_list:
            instruction = {
                "id": instructions['id'],
                "show_name": instructions['show_name'],
                "industrial_type": instructions['industrial_type'],  # 指令类型
                "important_level": instructions['important_level'],  # 重要性等级
                "issued_time": localizeStrTime(request, instructions['issued_time'],
                                               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"),  # 指令创建时间
                "run_time": localizeStrTime(request, instructions['run_time'],
                                            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"),  # 指令执行时间
                "special_data": instructions['special_data']  # 特殊数据
            }
            if instruction['special_data'] is None:
                instruction['special_data'] = ""
            result_list.append(instruction)

        _logger.debug("End vmc_instructions_issued ...")
        return {
            "records": result_list
        }

    # 指令下发状态更新接口 by dazhao
    @predispatch
    def vmc_instructions_issued_update(self, *args, **kwargs):
        _logger.debug("Begin  vmc_instructions_issued_update ...")
        status_list = kwargs['status_list']
        """
        status_list = [{'id': id, 'status': status},]
        status取值含义：
            ('confirm', '下发')
            ('get_successful', '已获取')
            ('run', '执行中')
            ('finished', '完成')
        """
        # 传值没有参数时候
        if not status_list or len(status_list) == 0:
            return {
                "error": "传参为空"
            }

        for status in status_list:
            request.cr.execute(
                """
                  UPDATE vmc_instructions_issued
                  SET instructions_state=%s
                  WHERE "id"=%s
                """, (status['status'], status['id'],))

        _logger.debug("End vmc_instructions_issued_update ...")
        return {
            "Success": "状态更新成功"
        }

    # 售货机配置 by dazhao
    @predispatch
    def vmc_settings(self, *args, **kwargs):
        _logger.debug("Begin  vmc_settings ...")
        machine_id = kwargs.get('machine_id')

        # 倒计时间查询
        request.cr.execute(
            """
            SELECT S.*
            FROM vmc_countdown_time_settings S,vmc_countdown_settings_machines_rel R
            WHERE R.machine_id=%s AND S."id"=R.setting_id
            """, (machine_id,))
        down_time_settings_dict = request.cr.dictfetchone()
        vmc_count_down_time_settings = {}
        if down_time_settings_dict is None:
            request.cr.execute(
                """
                select * from vmc_countdown_time_settings where id not in(
                select setting_id from vmc_countdown_settings_machines_rel)
                """)
            down_time_settings_dict = request.cr.dictfetchone()  # 如果没有找到machine_id，使用数据库中默认设置
        if down_time_settings_dict is not None:  # 如果不为空，赋值；如果连默认设置都是空，不做处理，返回空
            vmc_count_down_time_settings['general_page_countdown'] = down_time_settings_dict[
                'general_page_countdown']  # 一般页面倒计时间
            vmc_count_down_time_settings['purchase_page_countdown'] = down_time_settings_dict[
                'purchase_page_countdown']  # 商品购买页面倒计时间
            vmc_count_down_time_settings['message_countdown_long'] = down_time_settings_dict[
                'message_countdown_long']  # 提示信息倒计时间(长)
            vmc_count_down_time_settings['message_countdown_short'] = down_time_settings_dict[
                'message_countdown_short']  # 提示信息倒计时间(短)
            vmc_count_down_time_settings['help_page_countdown'] = down_time_settings_dict[
                'help_page_countdown']  # 帮助页面倒计时间
            vmc_count_down_time_settings['advertising_countdown'] = down_time_settings_dict[
                'advertising_countdown']  # 广告倒计时间

        # 机器定时重启时间
        request.cr.execute(
            """
            SELECT S.*
            FROM vmc_reset_time_settings S,vmc_reset_settings_machines_rel R
            WHERE R.machine_id=%s AND S."id"=R.setting_id
            """, (machine_id,))
        count_down_time_settings_dict = request.cr.dictfetchone()
        vmc_reset_time_settings = {}
        if count_down_time_settings_dict is None:
            request.cr.execute(
                """
                select * from vmc_reset_time_settings where id not in(
                select setting_id from vmc_reset_settings_machines_rel)
                """)
            count_down_time_settings_dict = request.cr.dictfetchone()  # 如果没有找到machine_id，使用数据库中默认设置
        if count_down_time_settings_dict is not None:  # 如果不为空，赋值；如果连默认设置都是空，不做处理，返回空
            vmc_reset_time_settings['time'] = count_down_time_settings_dict['time']  # 时间
            vmc_reset_time_settings['reset_time'] = count_down_time_settings_dict['reset_time']  # 重启时间

        # 安全距离
        request.cr.execute(
            """
            SELECT S.*
            FROM vmc_settings S,vmc_settings_machines_rel R
            WHERE R.machine_id=%s AND S."id"=R.setting_id
            """, (machine_id,))
        check_distance_dict = request.cr.dictfetchone()
        check_distance = 0
        if check_distance_dict is None:
            request.cr.execute(
                """
                select * from vmc_settings where id not in(
                select setting_id from vmc_settings_machines_rel)
                """)
            check_distance_dict = request.cr.dictfetchone()  # 如果没有找到machine_id，使用数据库中默认设置
        if check_distance_dict is not None:
            check_distance = check_distance_dict['check_distance']

        # 客服电话
        request.cr.execute(
            """
            SELECT C.customer_phone
            FROM vmc_machines M,res_company C
            WHERE M.id=%s AND M.company_id=C.id
            """, (machine_id,))
        customer_phone_dict = request.cr.dictfetchone()
        customer_phone = ''
        if customer_phone_dict is not None:
            customer_phone = customer_phone_dict['customer_phone'] if bool(
                customer_phone_dict['customer_phone']) else ''

        # 支付方式
        request.cr.execute(
            """
            SELECT *
            FROM vmc_machines
            WHERE id=%s
            """, (machine_id,))
        payment_way_dict = request.cr.dictfetchone()
        payment_way = {  # 默认什么方式都不支持
            "payment_weixin": 0,
            "payment_cash": 0,
            "payment_alipay": 0,
            "payment_wangbi": 0
        }
        if payment_way_dict is not None:
            payment_way['payment_alipay'] = int(payment_way_dict['payment_alipay'])
            payment_way['payment_weixin'] = int(payment_way_dict['payment_weixin'])
            payment_way['payment_wangbi'] = int(payment_way_dict['payment_wangbi'])
            payment_way['payment_cash'] = int(payment_way_dict['payment_cash'])

        return {
            'payment_way': payment_way,  # 支付方式
            'vmc_count_down_time_settings': vmc_count_down_time_settings,  # 倒计时间设置
            'vmc_reset_time_settings': vmc_reset_time_settings,  # 机器定时重启时间设置
            'check_distance': check_distance,  # 浮点类型, 安全距离设置
            'customer_phone': customer_phone  # 客服电话
        }

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

    # 查看存取款历史接口
    def vmc_account_history(self, *args, **kwargs):  # 售货机初始化
        _logger.debug("Begin vmc_account_history ...")
        record = {}
        if 'supply_id' in kwargs:
            supply_id = int(kwargs['supply_id'])
            sql = """with a as (select type,0 as amount,portable_coin_100cent,portable_coin_50cent,machine_coin_100cent ,machine_coin_50cent
                        from vmc_money_record where adjust_stock_id='%s' and type in ('giveby_supply')
                        union all
                        select type,amount,0,0, 0 ,0
                        from vmc_money_record where adjust_stock_id='%s' and type in ('get'))
                        select COALESCE (round(cast(sum(amount) as numeric),2),0) as amount,
                        COALESCE(sum(portable_coin_100cent),0) as portable_coin_100cent,
                        COALESCE(sum(portable_coin_50cent),0) as portable_coin_50cent,
                        COALESCE(sum(machine_coin_100cent),0) as machine_coin_100cent,
                        COALESCE(sum(machine_coin_50cent),0) as machine_coin_50cent from a """ % (
            supply_id, supply_id)
            request.cr.execute(sql)
            fet = request.cr.dictfetchone()
            if fet:
                record = fet
                amount = fet['amount']
                if amount != 0.0:
                    sql = """
                                select create_date
                                from vmc_money_record
                                where adjust_stock_id='%s' and type = 'get'
                                """ % (supply_id,)
                    request.cr.execute(sql)
                    r = request.cr.dictfetchone()
                    money_withdrawn_time = r['create_date'] or ""
                    if money_withdrawn_time != "":
                        money_withdrawn_time = localizeStrTime(request, money_withdrawn_time, "%Y-%m-%d %H:%M:%S",
                                                               "%Y-%m-%d %H:%M:%S")
                    record.update(
                        {
                            "money_withdrawn_time": money_withdrawn_time
                        }
                    )
            else:
                record = {'amount': 0, 'portable_coin_100cent': 0, 'portable_coin_50cent': 0,
                          'machine_coin_50cent': 0, 'machine_coin_100cent': 0}
        else:
            record = {'error': '请传补货单ID'}
        _logger.debug("End vmc_account_history ...\n")
        return record
