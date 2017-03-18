# -*- coding=utf8 -*-
# Author: sunyang
from datetime import datetime, timedelta, tzinfo
import time
from time import gmtime
import datetime as dtm
import hashlib
import logging
import re
import traceback

import pytz

from odoo import http
from odoo.http import request
from odoo import SUPERUSER_ID


def localizeStrTime(request, utcTimeStr, fromFormat, toFormat):
    """
    localize utc a time str gotten from database
    params:
    request : httprequest
    utcTimeStr:"2016-01-14 00:00:00"
    fromFormat:"%Y-%m-%d %H:%M:%S"
    toFormat:"%Y-%m-%d %H:%M:%S"
    """
    utc = pytz.timezone('UTC')
    # context_tz = pytz.timezone(request.context['tz'] or u'Asia/Shanghai')
    context_tz = pytz.timezone(u'Asia/Shanghai')
    utc_time = utc.localize(datetime.strptime(utcTimeStr, fromFormat), is_dst=False)  # UTC = no DST
    localized_time = utc_time.astimezone(context_tz)  # 将utc_time转为 context_tz时区的时间
    localized_time_str = localized_time.strftime(toFormat)
    return localized_time_str


# 将本地时间转为UTC时间
def utcStrTime(request, localTimeStr, fromFormat, toFormat):
    """
    localize utc a time str gotten from database
    params:
    request : httprequest
    utcTimeStr:"2016-01-14 00:00:00"
    fromFormat:"%Y-%m-%d %H:%M:%S"
    toFormat:"%Y-%m-%d %H:%M:%S"
    """
    context_tz = pytz.timezone(u'Asia/Shanghai')
    utc = pytz.timezone('UTC')
    local_time = context_tz.localize(datetime.strptime(localTimeStr, fromFormat))  # UTC = no DST 将navtive time加上时区信息
    utc_time = local_time.astimezone(utc)
    utc_time_str = utc_time.strftime(toFormat)
    return utc_time_str


# 验证是不是手机号
def is_phone(phone):
    # pattern = re.compile('^0\d{2,3}\d{7,8}$|^1[358]\d{9}$|^147\d{8}')
    pattern = re.compile('^1[3|4|5|8|7|9][0-9]\d{4,8}$')
    match = pattern.match(phone)
    if match:
        return True
    return False


# 是否加区号的固话
def is_landline(phone):
    pattern = re.compile("^0(10|2[0-5789]|\\d{3})\\d{7,8}$")
    match = pattern.match(phone)
    if match:
        return True
    return False


# 是否三大运营商手机号
def is_mobile(phone):
    # pattern = re.compile(
    #     "^13[5-9]\d{8}$|^134[0-8]\d{7}$|^147\d{8}$|^15[0-27-9]\d{8}$|^18[2378]\d{8}$|"  # 移动手机号
    #                     "^13[0-2]\d{8}$|^145\d{8}$|^15[56]\d{8}$|^18[56]\d{8}$|"  # 联通手机号
    #                     "^133\d{8}$|^153\d{8}$|^18[09]\d{8}$|")  # 电信手机号
    # match = pattern.match(phone)
    # if match:
    #     return True
    # return False
    return is_phone(phone)


def hash_string(str1):
    # MD5
    hash_obj = hashlib.md5()
    hash_obj.update(str1)
    return hash_obj.hexdigest()


# 获取record里面的图片url
def image_url(record, field, size=None, context=None):
    """Returns a local url that points to the image field of a given browse record."""
    sudo_record = record.sudo()
    sha = hashlib.sha1(getattr(sudo_record, '__last_update')).hexdigest()[0:7]
    size = '' if size is None else '/%s' % size
    return 'web/image/%s/%s/%s%s?unique=%s' % (record._name, record.id, field, size, sha)


def createstock(request, self, vals_list):
    move_obj = request.env['stock.move']
    move_ids = []
    for move_vals in vals_list:
        move_ids += [move_obj.create(move_vals).id]
    self.action_confirm(request.cr, SUPERUSER_ID, move_ids)
    return move_ids


# 开始查询utc时间，从x天前零时开始进行订单查询, x为外部参数
def utctime_start_lastdays(days_backwards):
    time_backwards = 3600 * 24 * (days_backwards - 1)
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    unix_timestamp_lastmonth = time.localtime(time.time() - time_backwards + timezone_interval)
    time_lastmonth = time.strftime(r'%Y-%m-%d', unix_timestamp_lastmonth)
    time_lastmonth = time_lastmonth + ' ' + '00:00:00'
    # 转换成utc时间
    time_lastmonth = utcStrTime(request, time_lastmonth, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
    return time_lastmonth


# 开始查询utc时间，从x小时前开始进行订单查询, x为外部参数
def utctime_start_lasthours(hours_backwards):
    time_backwards = 3600 * (hours_backwards - 1)
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    unix_timestamp_lasthours = time.localtime(time.time() - time_backwards + timezone_interval)
    time_lasthours = time.strftime(r'%Y-%m-%d %H:', unix_timestamp_lasthours)
    time_lasthours = time_lasthours + '00:00'
    # 转换成utc时间
    time_lasthours = utcStrTime(request, time_lasthours, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
    return time_lasthours


# 开始查询时间(北京时间)，从x小时前开始进行订单查询, x为外部参数
def cn_time_start_lasthours(hours_backwards):
    time_backwards = 3600 * (hours_backwards - 1)
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    unix_timestamp_lasthours = time.localtime(time.time() - time_backwards + timezone_interval)
    time_lasthours = time.strftime(r'%Y-%m-%d %H:', unix_timestamp_lasthours)
    time_lasthours = time_lasthours + '00:00'
    # 转换成utc时间
    return time_lasthours


# 查询过去x天订单成交总额前y名的售货机, x,y 为外部参数
def vmc_lastdays_ranking_list(days_backwards, vmc_order, offset, limit, order):
    # 当前的服务器UTC日期时间
    t = time.gmtime()
    time_end = time.strftime(r'%Y-%m-%d %H:%M:%S', t)
    time_lastmonth = utctime_start_lastdays(days_backwards)
    # puq desc,puq asc,ps desc,ps asc,puq 销量 ps 销售额
    # 查询过去x天订单成交总额前y名的售货机, x y为外部参数
    request.cr.execute(
        """select machine_id as vmc_id, sum(amount_total) as ps, count(id) as puq
           from """ + vmc_order + """
           where payment_status = '已支付' and create_time >= %s and create_time <= %s
           group by machine_id
           order by """ + order + """
           offset %s
           limit %s;
                """,
        (time_lastmonth, time_end, offset, limit))
    result = request.cr.dictfetchall()
    vmc_ranking_list = []
    # 查询每个售货机的名称和机型
    for vmc in result:
        vmc_id = vmc['vmc_id']
        request.cr.execute(
            """select vm.vmc_brand, rp.name
               from vmc_machines as vm, res_partner as rp
               where vm.id = %s and vm.partner_id = rp.id
            """,
            (vmc_id,))
        vmc_info = request.cr.dictfetchall()
        vmc['vmc_name'] = vmc_info[0]['name']
        vmc['vmc_brand'] = vmc_info[0]['vmc_brand']
        vmc_ranking_list.append(vmc)
    return vmc_ranking_list


# 开始查询时间，从当天北京时间零时开始进行订单查询
def utctime_start_thisday():
    # 当前北京时间
    current_time = datetime.utcnow() - timedelta(hours=-8)
    current_hour = current_time.hour
    # 时间戳间隔为1个小时
    timestamp_interval = 3600
    time_format = "%Y-%m-%d %H:%M:%S"
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    # 北京时间当天0点
    timestamp_start = gmtime(time.time() + timezone_interval)
    date_start = time.strftime(r'%Y-%m-%d', timestamp_start)
    time_start = date_start + ' ' + '00:00:00'
    # 转换成utc时间字符串形式
    initial_time_start = utcStrTime(request, time_start, time_format, time_format)
    return initial_time_start


# 开始查询时间，从本周一北京时间零时开始进行订单查询
def utctime_start_thisweek():
    # 当前时间
    struct_time = time.localtime(time.time())
    year = struct_time.tm_year
    month = struct_time.tm_mon
    day = struct_time.tm_mday
    dt = dtm.date(year, month, day)
    # 当前为周几 int类型
    weekday = dt.isocalendar()[2]
    # 开始查询时间，从本周一零时开始进行订单查询
    time_backwards = 3600 * 24 * (weekday - 1)
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    unix_timestamp_thisweek = time.localtime(time.time() - time_backwards + timezone_interval)
    time_thisweek = time.strftime(r'%Y-%m-%d', unix_timestamp_thisweek)
    time_thisweek = time_thisweek + ' ' + '00:00:00'
    # 转换成utc时间
    time_thisweek = utcStrTime(request, time_thisweek, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
    return time_thisweek


# 开始查询时间，从本月1号北京时间零时开始进行订单查询
def utctime_start_thismonth():
    # 当前时间
    current_time = datetime.utcnow() - timedelta(hours=-8)
    # 当前为几号 int类型
    monthday = current_time.day
    # 开始查询时间，从本月一号零时开始进行订单查询
    time_backwards = 3600 * 24 * (monthday - 1)
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    unix_timestamp_thisweek = time.localtime(time.time() - time_backwards + timezone_interval)
    timestart_thismonth = time.strftime(r'%Y-%m-%d', unix_timestamp_thisweek)
    timestart_thismonth = timestart_thismonth + ' ' + '00:00:00'
    # 转换成utc时间
    timestart_thismonth = utcStrTime(request, timestart_thismonth, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
    return timestart_thismonth


# 北京时间0点
def cn_strftime_zeropoint():
    # 当前的服务器utc时间
    t = time.time()
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    today_date = time.strftime(r'%Y-%m-%d', time.localtime(t + timezone_interval))
    # 今日零时作为订单查询起始时间
    time_start = today_date + ' ' + '00:00:00'
    return time_start


# 当前月天
def cn_current_date():
    # 当前的服务器utc时间
    t = time.time()
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    current_date = time.strftime(r'%m-%d', time.localtime(t + timezone_interval))
    return current_date

# 当前年月天
def cn_current_year_and_date():
    # 当前的服务器utc时间
    t = time.time()
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    current_date = time.strftime(r'%Y-%m-%d', time.localtime(t + timezone_interval))
    return current_date


# 昨日年月天
def cn_yesterday_year_and_date():
    # 当前的服务器utc时间
    t = time.time()
    # 北京时间和 utc 时间的时差
    timezone_interval = 8 * 3600
    # 昨日与今日的时间间隔
    time_interval = 24 * 3600
    yesterday_date = time.strftime(r'%Y-%m-%d', time.localtime(t + timezone_interval - time_interval))
    return yesterday_date


# utc时间0点
def utc_strftime_zeropoint():
    time_start = cn_strftime_zeropoint()
    # 转换成utc时间
    time_start = utcStrTime(request, time_start, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
    return time_start


# 过去x天售货机每日销量, 销售额
def sales_amount_and_count(days_backwards, vmc_order):
    records = []
    for i in range(days_backwards):
        record = {}
        # 时间间隔为1天
        time_interval = 3600 * 24
        # 北京时区与 utc 时区 时间差
        timezone_interval = 3600 * 8
        # 订单查询起始时间
        timestamp_start = time.localtime(time.time() - time_interval * i + timezone_interval)
        # 日期格式为年-月-日
        date_start = time.strftime(r'%Y-%m-%d', timestamp_start)
        # 日期格式为月-日
        date = time.strftime(r'%m/%d', timestamp_start)
        time_start = date_start + ' ' + '00:00:00'
        # 转换成utc时间
        time_start = utcStrTime(request, time_start, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
        # 当前服务器日期时间
        t = time.time()
        # 订单查询终止时间
        timestamp_end = time.localtime(t - time_interval * i + timezone_interval)
        date_end = time.strftime(r'%Y-%m-%d', timestamp_end)
        time_end = date_end + ' ' + '23:59:59'
        # 转换成utc时间
        time_end = utcStrTime(request, time_end, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
        request.cr.execute(
            """select round(cast(sum(amount_total) as numeric),2) as sales_amount, count(id) as sales_count
               from """ + vmc_order + """
               where payment_status = '已支付' and create_time >= %s and create_time <= %s""",
            (time_start, time_end,))
        result = request.cr.dictfetchone()
        sales_amount = result['sales_amount']
        sales_count = result['sales_count']
        sales_count = result['sales_count']
        record['sales_amount'] = sales_amount or 0.00
        record['sales_count'] = sales_count or 0.00
        record['date'] = date
        records.insert(0, record)
    return records


# 过去x小时售货机每日销量, 销售额
def sales_amount_and_count(hours_backwards, vmc_order):
    records = []
    for i in range(hours_backwards):
        record = {}
        # 时间间隔为1小时
        time_interval = 3600
        # 北京时区与 utc 时区 时间差
        timezone_interval = 3600 * 8
        # 订单查询起始时间
        timestamp_start = time.localtime(time.time() - time_interval * i + timezone_interval)
        # 日期格式为年-月-日
        date_start = time.strftime(r'%Y-%m-%d', timestamp_start)
        # 日期格式为月-日
        date = time.strftime(r'%m/%d', timestamp_start)
        time_start = date_start + ' ' + '00:00:00'
        # 转换成utc时间
        time_start = utcStrTime(request, time_start, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
        # 当前服务器日期时间
        t = time.time()
        # 订单查询终止时间
        timestamp_end = time.localtime(t - time_interval * i + timezone_interval)
        date_end = time.strftime(r'%Y-%m-%d', timestamp_end)
        time_end = date_end + ' ' + '23:59:59'
        # 转换成utc时间
        time_end = utcStrTime(request, time_end, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")
        request.cr.execute(
            """select round(cast(sum(amount_total) as numeric),2) as sales_amount, count(id) as sales_count
               from """ + vmc_order + """
               where payment_status = '已支付' and create_time >= %s and create_time <= %s""",
            (time_start, time_end,))
        result = request.cr.dictfetchone()
        sales_amount = result['sales_amount']
        sales_count = result['sales_count']
        record['sales_amount'] = sales_amount or 0.00
        record['sales_count'] = sales_count or 0.00
        record['date'] = date
        records.insert(0, record)
    return records
