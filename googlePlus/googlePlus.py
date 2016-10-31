# -*- coding:UTF-8  -*-
"""
Google Plus图片爬虫
https://plus.google.com/
@author: hikaru
email: hikaru870806@hotmail.com
如有问题或建议请联系
"""
from common import log, robot, tool
import os
import re
import threading
import time
import traceback

ACCOUNTS = []
GET_IMAGE_URL_COUNT = 100  # 单次获取最新的N张照片,G+ 限制最多1000张
TOTAL_IMAGE_COUNT = 0
GET_IMAGE_COUNT = 0
IMAGE_TEMP_PATH = ""
IMAGE_DOWNLOAD_PATH = ""
VIDEO_TEMP_PATH = ""
VIDEO_DOWNLOAD_PATH = ""
NEW_SAVE_DATA_PATH = ""
IS_SORT = True


# 获取一页相册中图片所在的picasweb地址列表
def get_one_page_album(account_id, token):
    index_url = "https://plus.google.com/_/photos/pc/read/"
    post_data = 'f.req=[["posts",null,null,"synthetic:posts:%s",3,"%s",null],[%s,1,null],"%s",null,null,null,null,null,null,null,2]' % (account_id, account_id, GET_IMAGE_URL_COUNT, token)
    index_page_return_code, index_page = tool.http_request(index_url, post_data)[:2]
    if index_page_return_code == 1:
        return index_page
    return None


# 获取相册页中的所有picasweb地址列表
def get_picasaweb_url_list(album_page):
    return re.findall('\[\["(https://picasaweb.google.com/[^"]*)"', album_page)


# 获取picasaweb页的album id
def get_picasaweb_page_album_id(account_id, picasaweb_url):
    message_page_return_code, message_page = tool.http_request(picasaweb_url)[:2]
    if message_page_return_code == 1:
        # 查找picasaweb页的album id
        album_archive_url = "https://get.google.com/albumarchive/pwa/%s/album/" % account_id
        return tool.find_sub_string(message_page, 'href="%s' % album_archive_url, '"')
    return None


# 获取album id对应相册存档页的全部图片地址列表
def get_image_url_list(account_id, album_id):
    album_archive_url = "https://get.google.com/albumarchive/pwaf/%s/album/%s?source=pwa" % (account_id, album_id)
    album_archive_page_return_code, album_archive_page = tool.http_request(album_archive_url)[:2]
    if album_archive_page_return_code == 1:
        return re.findall('<img src="([^"]*)"', album_archive_page)
    return None


# 重组URL并使用最大分辨率
# https://lh3.googleusercontent.com/uhGpzweN4P7b8KG042-XfksSgpDW6qKtDSIGo-HV1EhVgwQnh1u0DCWEERdlavj0NEusMwwn8OmJnRw=w165-h220-rw
# ->
# https://lh3.googleusercontent.com/uhGpzweN4P7b8KG042-XfksSgpDW6qKtDSIGo-HV1EhVgwQnh1u0DCWEERdlavj0NEusMwwn8OmJnRw=w9999-h9999
# wXXXX-hXXXX 显示分辨率, -rw 使用webp格式
def generate_max_resolution_image_url(image_url):
    return image_url.split("=")[0] + "=w9999-h9999"


class GooglePlus(robot.Robot):
    def __init__(self):
        global GET_IMAGE_COUNT
        global IMAGE_TEMP_PATH
        global IMAGE_DOWNLOAD_PATH
        global NEW_SAVE_DATA_PATH
        global IS_SORT

        sys_config = {
            robot.SYS_DOWNLOAD_IMAGE: True,
            robot.SYS_SET_PROXY: True,
        }
        robot.Robot.__init__(self, sys_config)

        # 设置全局变量，供子线程调用
        GET_IMAGE_COUNT = self.get_image_count
        IMAGE_TEMP_PATH = self.image_temp_path
        IMAGE_DOWNLOAD_PATH = self.image_download_path
        IS_SORT = self.is_sort
        NEW_SAVE_DATA_PATH = robot.get_new_save_file_path(self.save_data_path)

    def main(self):
        global ACCOUNTS

        # 解析存档文件
        # account_id  image_count  album_id  (account_name)  (file_path)
        account_list = robot.read_save_data(self.save_data_path, 0, ["", "0", "0"])
        ACCOUNTS = account_list.keys()

        # 循环下载每个id
        main_thread_count = threading.activeCount()
        for account_id in sorted(account_list.keys()):
            # 检查正在运行的线程数
            while threading.activeCount() >= self.thread_count + main_thread_count:
                if robot.is_process_end() == 0:
                    time.sleep(10)
                else:
                    break

            # 提前结束
            if robot.is_process_end() > 0:
                break

            # 开始下载
            thread = Download(account_list[account_id], self.thread_lock)
            thread.start()

            time.sleep(1)

        # 检查除主线程外的其他所有线程是不是全部结束了
        while threading.activeCount() > main_thread_count:
            time.sleep(10)

        # 未完成的数据保存
        if len(ACCOUNTS) > 0:
            new_save_data_file = open(NEW_SAVE_DATA_PATH, "a")
            for account_id in ACCOUNTS:
                new_save_data_file.write("\t".join(account_list[account_id]) + "\n")
            new_save_data_file.close()

        # 删除临时文件夹
        tool.remove_dir(IMAGE_TEMP_PATH)

        # 重新排序保存存档文件
        robot.rewrite_save_file(NEW_SAVE_DATA_PATH, self.save_data_path)

        log.step("全部下载完毕，耗时%s秒，共计图片%s张" % (self.get_run_time(), TOTAL_IMAGE_COUNT))


class Download(threading.Thread):
    def __init__(self, account_info, thread_lock):
        threading.Thread.__init__(self)
        self.account_info = account_info
        self.thread_lock = thread_lock

    def run(self):
        global TOTAL_IMAGE_COUNT

        account_id = self.account_info[0]
        if len(self.account_info) >= 4 and self.account_info[3]:
            account_name = self.account_info[3]
        else:
            account_name = self.account_info[0]
        if len(self.account_info) >= 5 and self.account_info[4]:
            account_file_path = self.account_info[4]
        else:
            account_file_path = ""

        try:
            log.step(account_name + " 开始")

            # 如果需要重新排序则使用临时文件夹，否则直接下载到目标目录
            if IS_SORT:
                image_path = os.path.join(IMAGE_TEMP_PATH, account_name)
            else:
                image_path = os.path.join(IMAGE_DOWNLOAD_PATH, account_file_path, account_name)

            # 图片下载
            image_count = 1
            key = ""
            first_album_id = "0"
            unique_list = []
            is_over = False
            need_make_download_dir = True
            while not is_over:
                # 获取一页相册
                album_page = get_one_page_album(account_id, key)
                if album_page is None:
                    log.error(account_name + " 无法访问相册页，token：%s" % key)
                    tool.process_exit()

                # 获取相册页中的所有picasweb地址列表
                picasaweb_url_list = get_picasaweb_url_list(album_page)

                log.trace(account_name + " 相册获取的所有picasaweb页：%s" % picasaweb_url_list)
                for picasaweb_url in picasaweb_url_list:
                    # 有可能拿到带authkey的，需要去掉
                    # https://picasaweb.google.com/116300481938868290370/2015092603?authkey\u003dGv1sRgCOGLq-jctf-7Ww#6198800191175756402
                    picasaweb_url = picasaweb_url.replace("\u003d", "=")

                    # 获取picasaweb页的album id
                    album_id = get_picasaweb_page_album_id(account_id, picasaweb_url)
                    if album_id is None:
                        log.error(account_name + " 第%s张图片，无法访问picasaweb页 %s" % (image_count, picasaweb_url))
                        continue
                    if not album_id:
                        log.error(account_name + " 第%s张图片，picasaweb页 %s 获取album id失败" % (image_count, picasaweb_url))
                        continue
                    log.trace(account_name + " picasaweb页 %s 的album id：%s" % (picasaweb_url, album_id))

                    # 检查是否已下载到前一次的图片
                    if int(album_id) <= int(self.account_info[2]):
                        is_over = True
                        break

                    # # 相同的album_id判断
                    if album_id in unique_list:
                        continue
                    else:
                        unique_list.append(album_id)
                    # 将第一个album_id做为新的存档记录
                    if first_album_id == "0":
                        first_album_id = album_id

                    # 获取album id对应相册存档页的全部图片地址列表
                    image_url_list = get_image_url_list(account_id, album_id)
                    if image_url_list is None:
                        log.error(account_name + " 第%s张图片，无法访问album id：%s 的相册存档页" % (image_count, album_id))
                        continue
                    if len(image_url_list) == 0:
                        log.error(account_name + " 第%s张图片，album id：%s 的相册存档页没有解析到图片" % (image_count, album_id))
                        continue

                    log.trace(account_name + " album id：%s 的相册存档页获取的所有图片：%s" % (album_id, image_url_list))
                    for image_url in list(image_url_list):
                        image_url = generate_max_resolution_image_url(image_url)

                        # 下载
                        log.step(account_name + " 开始下载第%s张图片 %s" % (image_count, image_url))

                        # 第一张图片，创建目录
                        if need_make_download_dir:
                            if not tool.make_dir(image_path, 0):
                                log.error(account_name + " 创建图片下载目录 %s 失败" % image_path)
                                tool.process_exit()
                            need_make_download_dir = False

                        if image_url.rfind("/") < image_url.rfind("."):
                            file_type = image_url.split(".")[-1]
                        else:
                            file_type = "jpg"
                        file_path = os.path.join(image_path, "%04d.%s" % (image_count, file_type))
                        if tool.save_net_file(image_url, file_path):
                            log.step(account_name + " 第%s张图片下载成功" % image_count)
                            image_count += 1
                        else:
                            log.error(account_name + " 第%s张图片 %s 下载失败" % (image_count, image_url))

                        # 达到配置文件中的下载数量，结束
                        if 0 < GET_IMAGE_COUNT < image_count:
                            is_over = True
                            break
                    if is_over:
                        break

                if not is_over:
                    # 查找下一页的token key
                    key_find = re.findall('"([.]?[a-zA-Z0-9-_]*)"', album_page)
                    if len(key_find) > 0 and len(key_find[0]) > 80:
                        key = key_find[0]
                    else:
                        # 不是第一次下载
                        if self.account_info[2] != "0":
                            log.error(account_name + " 没有找到下一页的token，将该页保存：")
                            log.error(album_page)
                        is_over = True

            log.step(account_name + " 下载完毕，总共获得%s张图片" % (image_count - 1))

            # 排序
            if IS_SORT and image_count > 1:
                destination_path = os.path.join(IMAGE_DOWNLOAD_PATH, account_file_path, account_name)
                if robot.sort_file(image_path, destination_path, int(self.account_info[1]), 4):
                    log.step(account_name + " 图片从下载目录移动到保存目录成功")
                else:
                    log.error(account_name + " 创建图片子目录 %s 失败" % destination_path)
                    tool.process_exit()

            # 新的存档记录
            if first_album_id != "0":
                self.account_info[1] = str(int(self.account_info[1]) + image_count - 1)
                self.account_info[2] = first_album_id

            # 保存最后的信息
            tool.write_file("\t".join(self.account_info), NEW_SAVE_DATA_PATH)
            self.thread_lock.acquire()
            TOTAL_IMAGE_COUNT += image_count - 1
            ACCOUNTS.remove(account_id)
            self.thread_lock.release()

            log.step(account_name + " 完成")
        except SystemExit, se:
            if se.code == 0:
                log.step(account_name + " 提前退出")
            else:
                log.error(account_name + " 异常退出")
        except Exception, e:
            log.error(account_name + " 未知异常")
            log.error(str(e) + "\n" + str(traceback.format_exc()))


if __name__ == "__main__":
    GooglePlus().main()
