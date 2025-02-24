import streamlit as st
import requests
import asyncio
import aiohttp
from aiohttp import ClientTimeout
from bs4 import BeautifulSoup
from similar import jaccard_similarity
import json
import pyperclip
import nest_asyncio
import time

# Initialize page config
st.set_page_config(page_title="🌟 多网站爬虫系统 (异步+缓存)", layout="wide")

# Apply nest_asyncio to make async work with Streamlit
nest_asyncio.apply()

# Constants
AFL_BASE_SEARCH_URL = 'https://api.c2k2y3nvy0-heuschena1-p1-public.model-t.cc.commerce.ondemand.com/occ/v2/B2C-AFL-DE/products/search'
AFL_BASE_DETAIL_URL = 'https://api.c2k2y3nvy0-heuschena1-p1-public.model-t.cc.commerce.ondemand.com/occ/v2/B2C-AFL-COM/products/'


# Async fetch function with timeout
async def fetch(session, url, params=None, headers=None):
    if headers is None:
        headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        timeout = ClientTimeout(total=10)
        async with session.get(url, params=params, headers=headers, timeout=timeout) as response:
            response.raise_for_status()
            return await response.text()
    except Exception as e:
        print(f"请求错误: {e}")
        return ""


# Cache the search results
@st.cache_data(show_spinner="🔍 正在搜索...", ttl=3600)
def cache_search_results(html_content, site, productname):
    if site == "maomao":
        return parse_mao_mao_results(html_content, productname)
    elif site == "asianfood":
        return parse_asian_food_results(html_content, productname)
    return []


# Cache the detail results
@st.cache_data(show_spinner="🔍 正在获取详情...", ttl=3600)
def cache_detail_results(detail_content, site):
    if site == "maomao":
        return parse_mao_mao_detail(detail_content)
    elif site == "asianfood":
        return parse_asian_food_detail(detail_content)
    return {}


# Async MaoMao search
async def async_search_mao_mao(productname):
    base_url = 'https://mao-mao.de/search'
    params = {'q': productname, 'options[prefix]': 'last'}

    async with aiohttp.ClientSession() as session:
        html = await fetch(session, base_url, params=params)
        return cache_search_results(html, "maomao", productname)


# Parse MaoMao search results
def parse_mao_mao_results(html, productname):
    soup = BeautifulSoup(html, 'html.parser')
    products = soup.select('div.grid-product')
    results = []

    for div in products:
        link_tag = div.find('a', class_='grid-item__link')
        name_tag = div.find('div', class_='grid-product__title')
        price_tag = div.select_one('span.grid-product__price--current span.visually-hidden')
        img_tag = div.find('img')

        if name_tag and link_tag:
            name = name_tag.get_text().strip()
            url = 'https://mao-mao.de' + link_tag.get('href')
            price = price_tag.get_text().strip() if price_tag else "N/A"
            image_src = img_tag.get('data-src') or img_tag.get('src')
            if image_src:
                image_src = f"https:{image_src}" if image_src.startswith("//") else f"https://mao-mao.de{image_src}"

            results.append({
                "name": name,
                "url": url,
                "price": price,
                "image_url": image_src,
                "similarity": jaccard_similarity(productname, name)
            })

    return sorted(results, key=lambda x: x['similarity'], reverse=True)


# Async MaoMao detail
async def async_get_mao_mao_detail(url):
    async with aiohttp.ClientSession() as session:
        html = await fetch(session, url)
        return cache_detail_results(html, "maomao")


# Parse MaoMao detail
def parse_mao_mao_detail(html):
    try:
        soup = BeautifulSoup(html, 'html.parser')

        # 基本信息
        product_name_tag = soup.find('h1', class_='product-single__title')
        price_tag = soup.select_one('span.product__price span.visually-hidden')

        # 初始化返回数据
        description = ""
        storage_info = ""
        preparation_info = ""

        # 首先尝试直接获取商品描述
        description_section = soup.find('div', {'id': 'dropdownContent1D'})
        if description_section:
            # 使用 at-rte 类来定位实际的描述文本
            rte_content = description_section.find('div', class_='at-rte')
            if rte_content:
                # 获取所有段落，但排除存储说明和净重信息
                paragraphs = []
                for p in rte_content.find_all('p'):
                    text = p.get_text(strip=True)
                    if text and not any(keyword in text.lower() for keyword in [
                        'aufbewahrungs', 'verwendungshinweise', 'nettogewicht',
                        'kühl und trocken', 'gramm', 'g das produktdesign'
                    ]):
                        paragraphs.append(text)
                description = '\n'.join(paragraphs)

        # 获取存储信息
        storage_section = soup.find(string=lambda text: text and 'Aufbewahrungs- und Verwendungshinweise' in text)
        if storage_section:
            # 获取紧跟在存储说明标题后的段落
            storage_p = storage_section.find_next('p')
            if storage_p:
                storage_info = storage_p.get_text(strip=True)

        # 如果上面的方法没找到存储信息，尝试其他方法
        if not storage_info:
            storage_keywords = ['Kühl und trocken lagern', 'Nach dem Öffnen']
            for keyword in storage_keywords:
                storage_text = soup.find(string=lambda text: text and keyword in text)
                if storage_text:
                    storage_info = storage_text.strip()
                    break

        # 获取配料信息
        ingredients = ""
        ingredients_sections = soup.find_all('span', class_='metafield-multi_line_text_field')
        if ingredients_sections:
            ingredients = ingredients_sections[0].get_text(strip=True)

        # 获取营养信息
        nutrition_info = {
            "Brennwert": "",
            "Fett": "",
            "- davon gesättigte Fettsäuren": "",
            "Kohlenhydrate": "",
            "- davon Zucker": "",
            "Eiweiß": "",
            "Salz": ""
        }

        if len(ingredients_sections) > 1:
            nutrition_text = ingredients_sections[1].get_text(separator='\n').strip()
            for line in nutrition_text.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    if key in nutrition_info:
                        nutrition_info[key] = value.strip().replace(',', '.')

        # 获取主图片
        image_src = None
        image_element = soup.select_one('div.product__main-photos img')
        if image_element:
            image_src = image_element.get('data-src') or image_element.get('src')
            if image_src:
                if image_src.startswith("//"):
                    image_src = f"https:{image_src}"
                elif image_src.startswith("/"):
                    image_src = f"https://mao-mao.de{image_src}"

        return {
            "name": product_name_tag.get_text().strip() if product_name_tag else "未知产品",
            "price": price_tag.get_text().strip() if price_tag else "N/A",
            "description": description,
            "storage_info": storage_info,
            "preparation_info": preparation_info,
            "ingredients": ingredients,
            "nutrition": nutrition_info,
            "image_url": image_src
        }

    except Exception as e:
        print(f"提取商品详情时发生错误: {e}")
        return {}


# Async AsianFoodLovers search
async def async_search_asian_food(productname):
    params = {
        'query': productname,
        'currentPage': 0,
        'pageSize': 21,
        'lang': 'de_DE',
        'curr': 'EUR'
    }

    async with aiohttp.ClientSession() as session:
        response_text = await fetch(session, AFL_BASE_SEARCH_URL, params=params)
        return cache_search_results(response_text, "asianfood", productname)


# Parse AsianFoodLovers search results
def parse_asian_food_results(json_text, productname):
    try:
        data = json.loads(json_text)
        results = []

        for product in data.get("products", []):
            code = product.get('code')
            name = product.get('commercialName', '')
            price = f"{product.get('price', {}).get('value', 'N/A')} €"

            results.append({
                "name": name,
                "url": code,
                "price": price,
                "similarity": jaccard_similarity(productname, name)
            })

        return sorted(results, key=lambda x: x['similarity'], reverse=True)
    except Exception as e:
        print(f"解析 AsianFoodLovers 结果时发生错误: {e}")
        return []


# Async AsianFoodLovers detail
async def async_get_asian_food_detail(productid):
    async with aiohttp.ClientSession() as session:
        response_text = await fetch(
            session,
            f"{AFL_BASE_DETAIL_URL}{productid}",
            params={'lang': 'de_DE', 'curr': 'EUR'}
        )
        return cache_detail_results(response_text, "asianfood")


# Parse AsianFoodLovers detail
def parse_asian_food_detail(json_text):
    try:
        data = json.loads(json_text)
        image_url = None
        if 'images' in data and len(data['images']) > 0:
            image_url = data['images'][0].get('url')

        return {
            "name": data.get('commercialName', ''),
            "price": f"{data.get('price', {}).get('value', '')} €",
            "description": data.get('description', ''),
            "ingredients": data.get('ingredients', ''),
            "image_url": image_url,
            "url": data.get('code', ''),
            "allergyInformation": '; '.join([a['description'] for a in data.get('allergyInformation', [])]),
            "origin": '; '.join([c['name'] for c in data.get('countriesOfOrigin', [])])
        }
    except Exception as e:
        print(f"解析 AsianFoodLovers 详情时发生错误: {e}")
        return {}


# Display search results
def display_search_results(results, site_name, get_detail_func):
    if not results:
        st.write(f"没有在 {site_name} 找到相关产品")
        return

    for idx, product in enumerate(results[:5]):
        with st.container():
            cols = st.columns([1, 3, 1])

            with cols[0]:
                if product.get("image_url"):
                    st.image(product["image_url"], width=150)

            with cols[1]:
                st.subheader(product["name"])
                st.markdown(f"💶 **价格**: {product['price']}")

            with cols[2]:
                detail_key = f"{site_name}_{idx}_detail"
                if st.button("🔍 查看详情", key=detail_key):
                    with st.spinner("正在获取详情..."):
                        details = asyncio.run(get_detail_func(product["url"]))
                        if details:
                            display_product_details(details, site_name)


# Display product details
def display_product_details(details, site_name):
    with st.expander("📋 产品详情", expanded=True):
        if details.get('image_url'):
            st.image(details['image_url'], width=300)

        st.markdown(f"💶 **价格**: {details['price']}")

        if details.get('description'):
            st.markdown("📝 **商品描述:**")
            st.markdown(details['description'])
            if st.button("📋 复制描述"):
                pyperclip.copy(details['description'])
                st.success("✅ 已复制到剪贴板!")

        if site_name == "MaoMao":
            if details.get('storage_info'):
                st.markdown("🏪 **存储说明:**")
                st.markdown(details['storage_info'])

            if details.get('preparation_info'):
                st.markdown("👨‍🍳 **准备说明:**")
                st.markdown(details['preparation_info'])

            if details.get('nutrition'):
                st.markdown("📊 **营养信息:**")
                for key, value in details['nutrition'].items():
                    if value:
                        st.markdown(f"- **{key}**: {value}")

        elif site_name == "AsianFoodLovers":
            if details.get('origin'):
                st.markdown("🌍 **产地:**")
                st.markdown(details['origin'])

            if details.get('allergyInformation'):
                st.markdown("⚠️ **过敏信息:**")
                st.markdown(details['allergyInformation'])

        if details.get('ingredients'):
            st.markdown("🧬 **配料表:**")
            st.markdown(details['ingredients'])


# 新增的显示函数
# def display_detail_section(title, content, key_prefix):
#     if content:
#         col1, col2 = st.columns([4, 1])
#         with col1:
#             st.markdown(f"{title}")
#             st.markdown(content)
#         with col2:
#             copy_status = st.empty()
#             if st.button("📋 复制", key=f"copy_{key_prefix}"):
#                 pyperclip.copy(content)
#                 copy_status.success("✅ 已复制!")
#                 time.sleep(2)
#                 copy_status.empty()


def display_detail_section(title, content, key_prefix):
    """显示详情部分的新函数，使用 Streamlit 的复制功能"""
    if content:
        st.markdown(f"{title}")
        st.markdown(content)
        st.code(content, language=None)  # 使用 st.code 来显示可复制的文本块


async def main():
    st.title("🌟 多网站爬虫系统 🌟 (异步 + 缓存)")
    
    # 初始化 session state
    if 'mao_mao_results' not in st.session_state:
        st.session_state.mao_mao_results = []
    if 'asian_food_results' not in st.session_state:
        st.session_state.asian_food_results = []
    if 'details_visibility' not in st.session_state:
        st.session_state.details_visibility = {}
    if 'details_data' not in st.session_state:
        st.session_state.details_data = {}
    if 'mao_page' not in st.session_state:
        st.session_state.mao_page = 1
    if 'asian_page' not in st.session_state:
        st.session_state.asian_page = 1

    # 搜索输入和按钮
    productname = st.text_input("🔍 输入要搜索的产品关键词:", key="search_input")
    if st.button("🔎 开始搜索", key="start_search_button") and productname:
        with st.spinner("正在异步搜索两个网站..."):
            st.session_state.mao_mao_results, st.session_state.asian_food_results = await asyncio.gather(
                async_search_mao_mao(productname),
                async_search_asian_food(productname)
            )
            st.session_state.details_visibility = {}
            st.session_state.details_data = {}
            st.session_state.mao_page = 1
            st.session_state.asian_page = 1

    # 使用选项卡显示结果
    tab1, tab2 = st.tabs(["🛒 MaoMao", "🛒 AsianFoodLovers"])

    # MaoMao 结果显示
    with tab1:
        if st.session_state.mao_mao_results:
            per_page = 5
            total_pages = (len(st.session_state.mao_mao_results) + per_page - 1) // per_page

            # 分页导航
            st.markdown("### 📄 页面导航")
            prev, page_info, next = st.columns([1, 3, 1])
            with prev:
                if st.button("⬅️ 上一页", key="mao_prev", disabled=st.session_state.mao_page <= 1):
                    st.session_state.mao_page -= 1
            with page_info:
                st.write(f"第 {st.session_state.mao_page} 页 / 共 {total_pages} 页")
            with next:
                if st.button("下一页 ➡️", key="mao_next", disabled=st.session_state.mao_page >= total_pages):
                    st.session_state.mao_page += 1

            start_idx = (st.session_state.mao_page - 1) * per_page
            end_idx = start_idx + per_page

            # 显示每个产品
            for idx, product in enumerate(st.session_state.mao_mao_results[start_idx:end_idx]):
                product_key = f"mao_{idx}_{product['name']}"
                
                with st.container():
                    cols = st.columns([1, 3, 1, 1])
                    
                    with cols[0]:
                        if product.get("image_url"):
                            st.image(product["image_url"], width=150)
                    
                    with cols[1]:
                        st.subheader(product["name"])
                        st.markdown(f"💶 **价格**: {product['price']}")
                    
                    with cols[2]:
                        if product.get("image_url"):
                            st.download_button(
                                "📥 下载图片",
                                data=requests.get(product["image_url"]).content,
                                file_name=f"{product['name']}.jpg",
                                key=f"download_{product_key}"
                            )
                    
                    with cols[3]:
                        if st.button("🔍 查看详情", key=f"detail_{product_key}"):
                            if product["name"] not in st.session_state.details_data:
                                with st.spinner("正在获取详情..."):
                                    st.session_state.details_data[product["name"]] = await async_get_mao_mao_detail(product["url"])
                            st.session_state.details_visibility[product["name"]] = \
                                not st.session_state.details_visibility.get(product["name"], False)

                    # 显示详情信息
                    if st.session_state.details_visibility.get(product["name"], False):
                        details = st.session_state.details_data[product["name"]]
                        if details:
                            with st.expander("📋 详细信息", expanded=True):
                                if details.get('image_url'):
                                    st.image(details['image_url'], width=300)

                                if details.get('description'):
                                    display_detail_section(
                                        "📝 **商品描述:**",
                                        details['description'],
                                        f"desc_{product_key}"
                                    )

                                if details.get('storage_info'):
                                    display_detail_section(
                                        "🏪 **存储说明:**",
                                        details['storage_info'],
                                        f"storage_{product_key}"
                                    )

                                if details.get('preparation_info'):
                                    display_detail_section(
                                        "👨‍🍳 **准备说明:**",
                                        details['preparation_info'],
                                        f"prep_{product_key}"
                                    )

                                if details.get('nutrition'):
                                    st.markdown("📊 **营养信息:**")
                                    nutrition_text = "\n".join(
                                        [f"{key}: {value}" for key, value in details['nutrition'].items() if value]
                                    )
                                    st.code(nutrition_text, language=None)
                                
                                if details.get('ingredients'):
                                    display_detail_section(
                                        "🧬 **配料表:**",
                                        details['ingredients'],
                                        f"ingredients_{product_key}"
                                    )

                                st.markdown(
                                    f"""🔗 **产品链接:** <a href="{product['url']}" target="_blank">{product['url']}</a>""",
                                    unsafe_allow_html=True
                                )
        else:
            st.info("暂无 MaoMao 搜索结果")

    # AsianFoodLovers 结果显示
    with tab2:
        if st.session_state.asian_food_results:
            per_page = 5
            total_pages = (len(st.session_state.asian_food_results) + per_page - 1) // per_page

            # 分页导航
            st.markdown("### 📄 页面导航")
            prev, page_info, next = st.columns([1, 3, 1])
            with prev:
                if st.button("⬅️ 上一页", key="afl_prev", disabled=st.session_state.asian_page <= 1):
                    st.session_state.asian_page -= 1
            with page_info:
                st.write(f"第 {st.session_state.asian_page} 页 / 共 {total_pages} 页")
            with next:
                if st.button("下一页 ➡️", key="afl_next", disabled=st.session_state.asian_page >= total_pages):
                    st.session_state.asian_page += 1

            start_idx = (st.session_state.asian_page - 1) * per_page
            end_idx = start_idx + per_page

            # 显示每个产品
            for idx, product in enumerate(st.session_state.asian_food_results[start_idx:end_idx]):
                product_key = f"afl_{idx}_{product['name']}"

                with st.container():
                    cols = st.columns([1, 3, 1])

                    # 产品图片
                    with cols[0]:
                        if product.get("image_url"):
                            st.image(product["image_url"], width=150)

                    # 产品基本信息
                    with cols[1]:
                        st.subheader(product["name"])
                        st.markdown(f"💶 **价格**: {product['price']}")

                    # 查看详情按钮
                    with cols[2]:
                        if st.button("🔍 查看详情", key=f"detail_{product_key}"):
                            if product["name"] not in st.session_state.details_data:
                                with st.spinner("正在获取详情..."):
                                    st.session_state.details_data[product["name"]] = await async_get_asian_food_detail(
                                        product["url"])
                            st.session_state.details_visibility[product["name"]] = \
                                not st.session_state.details_visibility.get(product["name"], False)

                    # 显示详情信息
                    if st.session_state.details_visibility.get(product["name"], False):
                        details = st.session_state.details_data[product["name"]]
                        if details:
                            with st.expander("📋 详细信息", expanded=True):
                                if details.get('image_url'):
                                    st.image(details['image_url'], width=300)

                                # 描述信息
                                if details.get('description'):
                                    st.markdown("📝 **商品描述:**")
                                    st.markdown(details['description'])
                                    if st.button("📋 复制描述", key=f"copy_desc_{product_key}"):
                                        pyperclip.copy(details['description'])
                                        st.success("✅ 已复制到剪贴板!")

                                # 产地信息
                                if details.get('origin'):
                                    st.markdown("🌍 **产地:**")
                                    st.markdown(details['origin'])

                                # 过敏信息
                                if details.get('allergyInformation'):
                                    st.markdown("⚠️ **过敏信息:**")
                                    st.markdown(details['allergyInformation'])

                                # 配料信息
                                if details.get('ingredients'):
                                    st.markdown("🧬 **配料表:**")
                                    st.markdown(details['ingredients'])
                                    if st.button("📋 复制配料", key=f"copy_ingredients_{product_key}"):
                                        pyperclip.copy(details['ingredients'])
                                        st.success("✅ 已复制到剪贴板!")

                                # 产品编码
                                st.markdown(f"🔗 **产品编码:** {details['url']}")
        else:
            st.info("暂无 AsianFoodLovers 搜索结果")


# 主程序入口
if __name__ == "__main__":
    asyncio.run(main())