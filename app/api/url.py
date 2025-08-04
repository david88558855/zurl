from fastapi import FastAPI, Form,Request,File, UploadFile
from fastapi.responses import RedirectResponse
from datetime import datetime
import aiohttp
from bs4 import BeautifulSoup
import asyncio
from app.middleware.click import increment_click_count
from app.middleware.deny import deny_uas
from app.config import templates
from sqlalchemy import desc
import asyncio

from app.utils.helper import *
import time
from app.models.conn import get_db
from pydantic import BaseModel,HttpUrl
from app.models.urls import Urls
import json
import re

class UrlItem(BaseModel):
    short_url: str = None
    long_url: str
    title: str = None
    description: str = None

class UrlSearchItem(BaseModel):
    filter: str
    keyword: str

# 限制的短链接名称
DENY_SHORT_URLS = ["api","init", "admin", "login", "logout", "register", "import", "export"]

class UrlAPI:
    def __init__(self):
        # 用于保存后台任务的集合，防止任务被垃圾回收
        self._background_tasks = set()

    # 缩短URL
    async def shorten_url(self, item: UrlItem, request: Request):
        # 获取当前时间戳
        current_time = int(time.time())
        created_at = current_time
        updated_at = current_time
        ip = get_client_ip(request)

        long_url = item.long_url.strip()
        # 正则验证是否是http://或https://开头的URL
        if not re.match(r"^(http://|https://)", long_url):
            return show_json(400, "不正确的链接！", {})

        # 如果短链接是空的
        if not item.short_url:
            item.short_url = random_string(4).lower()  # 生成一个随机的短链接
        else:
            item.short_url = item.short_url.strip().lower()
            # 正则验证short_url是否合法，只能是小写字母或数字或中横线、下划线组合，不超过32位
            if not re.match(r"^[a-z0-9_-]{1,32}$", item.short_url):
                return show_json(400, "短链接不合法！", {})
            # 检查短链接是否在限制列表中
            if item.short_url in DENY_SHORT_URLS:
                return show_json(400, f"短链接：{item.short_url} 是系统保留的，请更换！", {})

        # 如果标题是空的，则使用长链接剔除协议和路径作为标题
        # if not item.title:
        #     item.title = item.long_url.split("//")[-1].split("/")[0]

        # 检查长链接是否已经存在于数据库中
        db = next(get_db())
        row = Urls.get_by_long_url(db, long_url)
        # 如果短链接已经存在，直接返回
        if row:
            return show_json(200, "success", {
                "short_url": row.short_url,
                "long_url": row.long_url,
                "title": row.title,
                "description": row.description,
            })
        
        # 创建新的Url对象
        url = Urls(
            short_url=item.short_url,
            long_url=long_url,
            title=item.title,
            description=item.description,
            created_at=created_at,
            updated_at=updated_at,
            ip=ip
        )

        # 将新创建的Url对象保存到数据库
        db.add(url)
        db.commit()
        db.refresh(url)
        # 获取链接的ID
        id = url.id
        db.close()

        # 异步启动更新链接标题和描述，前提是这两者为空
        if not item.title:
            task = asyncio.create_task(self.update_url_metadata(id=id, long_url=long_url))
            # 保持引用，防止被垃圾回收
            self._background_tasks.add(task)
            # 任务完成后自动清理引用
            task.add_done_callback(self._background_tasks.discard)
        

        return show_json(200, "success", {
            "short_url": url.short_url,
            "long_url": long_url,
            "title": url.title,
            "description": url.description,
        })
    
    # 根据ID和长链接，然后获取标题和描述并更新
    async def update_url_metadata(self, id: int, long_url: str):
        # 获取当前时间戳
        current_time = int(time.time())
        updated_at = current_time

        # 获取URL的标题和描述
        data = await self.get_url_info(long_url)
        title = data.get("title", "")
        description = data.get("description", "")

        db = next(get_db())
        url = Urls.get_by_id(db, id)
        
        if not url:
            return None
        
        # 更新标题和描述
        url.title = title
        url.description = description
        url.updated_at = updated_at
        
        db.commit()
        db.refresh(url)
        db.close()

        return None
    
    # 获取短链参数查询并跳到长链接，用302跳转
    async def redirect(self, short_url: str, request: Request):
        # 禁止的UA
        result = await deny_uas(request)
        print(f"deny_uas result: {result}")  # 调试输出
        if result:
            # 渲染403错误页面并直接返回，阻止后续执行
            return templates.TemplateResponse(
                name="error_pages/deny.html",
                context={"request": request},
                status_code=403
            )

        db = next(get_db())
        row = Urls.get_by_short_url(db, short_url)
        
        # 如果短链接不存在，返回404
        if not row:
            return templates.TemplateResponse(
                name="error_pages/404.html",
                context={"request": request},
                status_code=404
            )

        # 更新访问次数和最后访问时间
        # row.visit_count += 1
        # row.last_visited_at = int(time.time())
        long_url = row.long_url
        db.commit()
        db.close()

        # 在函数内部调用点击计数
        await increment_click_count(short_url)

        # 返回302重定向到长链接
        return RedirectResponse(url=long_url, status_code=302)
    
    # 导入数据
    async def import_data(self,file: UploadFile = File(...)):
        try:
            # 读取文件内容
            contents = await file.read()
            json_str = contents.decode('utf-8')
            # 文件是json格式，解析json数据
            json_data = json.loads(json_str)
            # 获取需要的数据
            datas = json_data[2:][0]["data"]
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as e:
            return show_json(400, f"JSON格式不支持或数据结构不正确！", {})
        
        # 获取datas的行数
        count = len(datas)
        # 遍历datas，然后一次性插入到数据库中
        db = next(get_db())
        for data in datas:
            timestamp = datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S").timestamp()
            # 创建新的Url对象
            url = Urls(
                short_url=data["keyword"],
                long_url=data["url"],
                title=data.get("title", ""),
                description="",
                created_at=timestamp,
                updated_at=timestamp,
                ip=data["ip"],
                clicks=data.get("clicks", 0)
            )
            # 将新创建的Url对象保存到数据库
            db.add(url)
        db.commit()
        db.close()

        return show_json(200, "success", count)
    
    # 获取列表，并进行分页,用page和limit进行分页
    def get_list(self, page: int = 1, limit: int = 10):
        try:
            db = next(get_db())
            # 获取总数
            total = db.query(Urls).count()
            # 获取数据，并按照ID降序排列
            urls = db.query(Urls) \
                .order_by(desc(Urls.id)) \
                .offset((page - 1) * limit) \
                .limit(limit) \
                .all()
            # 返回数据
            return show_json(200, "success", {
                "total": total,
                "page": page,
                "limit": limit,
                "urls": urls
            })
        finally:
            db.close()
    
    # 清空所有链接
    def clear_all(self):
        db = next(get_db())
        db.query(Urls).delete()
        db.commit()
        db.close()
        return show_json(200, "All URLs cleared successfully", {})
    
    # 根据短链接删除单个链接
    def delete_by_short_url(self, short_url: str):
        db = next(get_db())
        url = Urls.get_by_short_url(db, short_url)
        if not url:
            return show_json(404, "Short URL not found", {})
        
        db.delete(url)
        db.commit()
        db.close()
        return show_json(200, "Short URL deleted successfully", {})
    
    # 提供获取URL的接口
    async def get_url_metadata(self, url: str):
        """
        获取URL的标题和描述
        
        Args:
            url: 要获取元数据的URL
            
        Returns:
            dict: 包含标题和描述的字典
        """
        # 如果URL是空的，直接返回空的标题和描述
        if not url:
            return show_json(400, "URL不能为空", {"title": "", "description": ""})
        
        data = await self.get_url_info(url)
        return show_json(200, "success", data)
    
    # 获取URL的标题和描述
    async def get_url_info(self, url: str):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': url,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            connector = aiohttp.TCPConnector(limit=100, force_close=True, enable_cleanup_closed=True)
            
            async with aiohttp.ClientSession(
                headers=headers, 
                timeout=timeout, 
                connector=connector,
                version=aiohttp.HttpVersion11  # 使用HTTP/1.1以确保兼容性
            ) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        html_content = await response.text()
                        soup = BeautifulSoup(html_content, 'html.parser')
                        
                        # 获取标题
                        title = ""
                        title_tag = soup.find('title')
                        if title_tag:
                            title = title_tag.get_text().strip()
                        
                        # 获取描述
                        description = ""
                        # 尝试获取meta description
                        desc_tag = soup.find('meta', attrs={'name': 'description'})
                        if not desc_tag:
                            desc_tag = soup.find('meta', attrs={'property': 'og:description'})
                        if desc_tag:
                            description = desc_tag.get('content', '').strip()

                        # 过滤下title和描述，避免被注入
                        title = re.sub(r'[\'"`=<>]', '', title)
                        description = re.sub(r'[\'"`=<>]', '', description)
                        
                        data = {
                            "title": title,
                            "description": description
                        }
                        # 返回标题和描述
                        return data
                    else:
                        data = {
                            "title": "",
                            "description": ""
                        }
                        return data
                        
        except asyncio.TimeoutError:
            data = {
                "title": "",
                "description": ""
            }
            return data
        except Exception as e:
            data = {
                "title": "",
                "description": ""
            }
            return data
        
    # 根据shorten_url查询单个链接信息
    def get_by_shorten_url(self, short_url: str):
        db = next(get_db())
        url = Urls.get_by_short_url(db, short_url)
        db.close()
        if not url:
            return show_json(404, "Short URL not found", {})
        
        return show_json(200, "success", url)

    # 更新短链接信息
    def update_url(self, id:int,item: UrlItem):
        db = next(get_db())
        url = Urls.get_by_id(db, id)

        if not url:
            return show_json(404, "Short URL not found", {})
        
        # 检查short_url是否已存在
        if item.short_url != url.short_url and Urls.check_short_url_exists(db, item.short_url):
            return show_json(400, f"短链接：{item.short_url} 已存在！", {})
        
        # 更新长链接、标题和描述
        url.long_url = item.long_url
        url.title = item.title
        url.short_url = item.short_url
        url.description = item.description
        url.updated_at = int(time.time())
        
        db.commit()
        db.refresh(url)
        db.close()
        
        return show_json(200, "Short URL updated successfully", {
            "short_url": url.short_url,
            "long_url": url.long_url,
            "title": url.title,
            "description": url.description,
        })
    
    # 查询短链接接口，可根据filter和keyword进行查询
    def search_urls(self, item: UrlSearchItem):
        db = next(get_db())
        query = db.query(Urls)
        db.close()
        
        # 根据短链接精准查询
        if item.filter == "short_url":
            query = query.filter(Urls.short_url == item.keyword)
        elif item.filter == "long_url":
            # 长链接模糊查询
            query = query.filter(Urls.long_url.like(f"%{item.keyword}%"))
        elif item.filter == "title":
            # 标题模糊查询
            query = query.filter(Urls.title.like(f"%{item.keyword}%"))
        
        urls = query.limit(30).all()

        if not urls:
            return show_json(404, "没有查询到任何匹配！", {})
        
        return show_json(200, "success", {
            "total": len(urls),
            "page": 1,
            "limit": 30,
            "urls": urls
        })
