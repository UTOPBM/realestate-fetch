from dotenv import load_dotenv
load_dotenv()
import requests
import xml.etree.ElementTree as ET
import mysql.connector
from datetime import datetime
import os
from flask import Flask, jsonify

app = Flask(__name__)

# 환경변수 유효성 검사
def validate_environment():
    required_vars = ['DECODED_API_KEY', 'DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME', 'DB_PORT']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        raise EnvironmentError(f"필수 환경변수가 설정되지 않았습니다: {', '.join(missing_vars)}")

def safe_cast(value, to_type, default=None):
    """안전한 타입 변환 함수"""
    try:
        return to_type(value) if value is not None else default
    except (ValueError, TypeError):
        return default

def fetch_and_store_data(lawd_cd, current_year, current_month):
    """지역 코드별 데이터 수집 및 저장 함수"""
    endpoint = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    decoded_api_key = os.environ['DECODED_API_KEY']
    deal_ymd = f"{current_year}{current_month:02d}"

    # 페이징 처리
    page_no = 1
    total_inserted = 0
    error_message = None  # 에러 메시지 초기화

    try:
        conn = mysql.connector.connect(**{
            'host': os.environ['DB_HOST'],
            'user': os.environ['DB_USER'],
            'password': os.environ['DB_PASSWORD'],
            'database': os.environ['DB_NAME'],
            'port': int(os.environ['DB_PORT']),
            'charset': 'utf8mb4',
            'collation': 'utf8mb4_general_ci'
        })
        cursor = conn.cursor()

        while True:
            params = {
                'serviceKey': decoded_api_key,
                'LAWD_CD': lawd_cd,
                'DEAL_YMD': deal_ymd,
                'numOfRows': '1000',
                'pageNo': str(page_no),
                'dataType': 'XML'
            }

            response = requests.get(endpoint, params=params)
            response.raise_for_status()
            root = ET.fromstring(response.content)

            items = root.findall('.//item')
            if not items:
                break

            for item in items:
                # 거래 월 검증
                deal_year = safe_cast(item.findtext('dealYear'), int)
                deal_month = safe_cast(item.findtext('dealMonth'), int)

                if deal_year != current_year or deal_month != current_month:
                    print(f"[경고] {deal_year}-{deal_month} 데이터 건너뛰기")
                    continue

                # 필드 추출
                apt_dong = item.findtext('aptDong')
                apt_nm = item.findtext('aptNm')
                build_year = safe_cast(item.findtext('buildYear'), int)
                deal_amount = safe_cast(item.findtext('dealAmount'), lambda x: x.replace(',', '')) if item.find('dealAmount') is not None else None
                deal_day = safe_cast(item.findtext('dealDay'), int)
                exclu_use_ar = safe_cast(item.findtext('excluUseAr'), float)
                floor = safe_cast(item.findtext('floor'), int)
                sgg_cd = item.findtext('sggCd')
                umd_nm = item.findtext('umdNm')

                # 중복 체크
                cursor.execute('''
                    SELECT 1 FROM real_estate
                    WHERE dealYear = %s AND dealMonth = %s AND dealDay = %s
                    AND sggCd = %s AND aptNm = %s AND excluUseAr = %s
                    AND floor = %s AND aptDong = %s
                    LIMIT 1
                ''', (deal_year, deal_month, deal_day, sgg_cd, apt_nm, exclu_use_ar, floor, apt_dong))

                if not cursor.fetchone():
                    cursor.execute('''
                        INSERT INTO real_estate (
                            aptDong, aptNm, buildYear, dealAmount,
                            dealDay, dealMonth, dealYear, excluUseAr,
                            floor, sggCd, umdNm
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (
                        apt_dong, apt_nm, build_year, deal_amount,
                        deal_day, deal_month, deal_year, exclu_use_ar,
                        floor, sgg_cd, umd_nm
                    ))
                    total_inserted += 1

            # 다음 페이지 확인
            if len(items) < 1000:
                break
            page_no += 1

        conn.commit()
        print(f"[성공] {lawd_cd} 지역: {total_inserted}건 저장")

    except Exception as e:
        error_message = f"[에러] {lawd_cd} 지역 처리 실패: {str(e)}"
        print(error_message)
        conn.rollback()
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

    return total_inserted, error_message  # 성공 건수와 에러 메시지 반환

@app.route('/run')
def run_data_collection():
    """/run 엔드포인트: 데이터 수집 및 저장 로직 실행"""
    validate_environment()

    gu_list = [
        '11110', '11140', '11170', '11200', '11215', '11230', '11260', '11290', '11305',
        '11320', '11350', '11380', '11410', '11440', '11470', '11500', '11530', '11545',
        '11560', '11590', '11620', '11650', '11680', '11710', '11740'
    ]

    now = datetime.now()
    current_year = now.year
    current_month = now.month

    print(f"▦▦▦ {current_year}년 {current_month}월 아파트 실거래가 수집 시작 ▦▦▦")
    results = {}
    total_inserted_all_regions = 0  # 모든 지역에서 총 저장된 건수를 추적합니다.

    for gu_code in gu_list:
        inserted_count, error_msg = fetch_and_store_data(gu_code, current_year, current_month)
        results[gu_code] = {
            'inserted_count': inserted_count,
            'error': error_msg
        }
        total_inserted_all_regions += inserted_count  # 각 지역별 저장 건수를 누적합니다.

    print("▦▦▦ 모든 지역 데이터 수집 완료 ▦▦▦")

    # 전체 결과에 총 저장 건수를 추가합니다.
    results['total_inserted_all_regions'] = total_inserted_all_regions

    return jsonify(results)

if __name__ == "__main__":
    # app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))  # Cloud Run에 맞게 포트 설정
    app.run(debug=True, host='0.0.0.0', port=8080) # 로컬 테스트용