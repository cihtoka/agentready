# 구조화 데이터와 조작 요소 가이드라인

## jsonld.why: 상품 구조화 데이터가 필요한 이유
상품 상세 페이지에 schema.org Product JSON-LD가 있으면 에이전트가 화면을 해석하지 않고도 상품명, 이미지, 가격, 재고를 정확히 읽는다. 구조화 데이터가 없으면 본문 문장에서 추측해야 해서 오인식과 환각이 늘고, 비교 구매 후보에서 빠지기 쉽다.

## jsonld.fix: 상품 구조화 데이터(JSON-LD) 추가 방법
head 안에 script type="application/ld+json" 블록을 추가하고 @type 을 Product 로 지정한다. name, image 와 함께 offers 안에 price, priceCurrency(KRW), availability 를 반드시 넣는다. 값은 화면에 보이는 값과 같아야 하며, 템플릿에서 상품 데이터로 자동 생성하는 것이 안전하다.

## jsonld.example: 상품 JSON-LD 예시 코드
나쁜 예는 상품명과 가격이 div 안의 문장으로만 있는 경우다. 좋은 예는 {"@type":"Product","name":"원목 사이드 테이블","offers":{"price":"89000","priceCurrency":"KRW","availability":"https://schema.org/InStock"}} 형태의 JSON-LD 블록이다.

## price.why: 가격 표기가 중요한 이유
가격은 에이전트가 상품을 비교하고 예산을 판단하는 핵심 값이다. 이미지로 된 가격이나 통화 단위 없는 숫자는 정가, 할인가, 배송비를 구분하지 못해 비교 구매에서 오류가 나거나 후보에서 제외된다.

## price.fix: 가격 마크업 수정 방법
가격 요소에 itemprop="price" 와 content 속성으로 숫자 값을 넣고, 화면 텍스트에는 원 단위를 함께 표기한다. 가격을 이미지로만 보여주지 않는다. 할인가와 정가가 함께 있으면 판매 가격에만 price 를 지정한다.

## price.example: 가격 마크업 예시
나쁜 예는 <div class="prc">129000</div> 처럼 숫자만 있는 경우다. 좋은 예는 <span itemprop="price" content="129000">129,000원</span> 처럼 값과 통화 단위를 모두 제공하는 경우다.

## availability.why: 재고 상태 표기가 필요한 이유
품절 상품을 추천하거나 재고를 확인하지 못해 구매를 포기하는 상황을 막으려면 재고 상태가 기계가 읽는 값이어야 한다. 본문 문장만으로는 에이전트가 구매 가능 여부를 확신하지 못한다.

## availability.fix: 재고 상태 구조화 방법
JSON-LD offers.availability 에 schema.org 값(InStock, OutOfStock, PreOrder)을 넣거나 itemprop="availability" 를 사용한다. 값은 반드시 실제 재고 시스템 기준으로 채우며 추정으로 채우지 않는다.

## availability.example: 재고 상태 예시
나쁜 예는 본문에 재고 12개 남음 문장만 있는 경우다. 좋은 예는 "availability": "https://schema.org/InStock" 와 화면의 재고 있음 표기를 함께 제공하는 경우다.

## clickables.why: 표준 클릭 요소가 필요한 이유
에이전트는 a, button 같은 표준 태그로 클릭 가능 여부를 판단한다. div, span, td 에 onclick 만 붙인 요소는 클릭 대상으로 인식되지 않아 검색, 상품 이동, 장바구니 담기에서 탐색이 끊긴다.

## clickables.fix: 클릭 요소를 표준 태그로 바꾸는 방법
이동은 a 태그의 href 로, 동작은 button 태그로 바꾼다. 검색창은 form 으로 감싸 제출 버튼을 두고, 장바구니 담기 버튼은 form action 이 장바구니 경로를 가리키게 한다. 기존 자바스크립트 핸들러는 button 의 onclick 으로 유지해도 된다.

## clickables.example: 링크와 버튼 변환 예시
나쁜 예는 <div onclick="location.href='product.html'">상품</div> 이다. 좋은 예는 <a href="product.html">상품</a> 이며, 담기는 <form action="cart.html" method="post"><button type="submit">장바구니 담기</button></form> 로 구성한다.

## labels.why: 입력 요소 라벨이 필요한 이유
입력 요소의 용도를 알아야 에이전트가 검색어, 수량, 옵션 값을 올바르게 채운다. placeholder 는 입력하면 사라지고 신뢰도가 낮아 라벨을 대신하지 못한다.

## labels.fix: 입력 라벨 연결 방법
label 의 for 와 입력 요소의 id 를 연결하거나 aria-label 을 입력 요소에 지정한다. select 와 수량 입력도 예외 없이 적용한다. 보이는 라벨을 두기 어렵다면 aria-label 로 용도를 밝힌다.

## labels.example: 라벨 예시 코드
나쁜 예는 <input type="text" placeholder="뭐 찾으세요?"> 이다. 좋은 예는 <label for="q">상품 검색</label><input id="q" type="search"> 이거나 <input aria-label="상품 검색"> 이다.
