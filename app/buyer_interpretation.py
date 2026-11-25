"""Interpret buyer form text once, before the durable request is created.

Only consume recognizable phrases. Exact configurations keep their suffix and
unrepresented clauses remain evidence gaps. Structured API writes are unchanged.
"""
import re

from app import contracts_v2 as v2
from app.domestic_search_plan import PUBLIC_CITIES

CITY = '|'.join(map(re.escape, PUBLIC_CITIES))
DELIVERY = rf'\b(?:for\s+|with\s+)?(?:deliver(?:ed|y)?|ship(?:ped|ping)?)\s+(?:to|in)\s+({CITY})\b'
CAPACITY = (r'(?<![\d.])(\d{1,5})\s*(GB|TB)\b(?:\s+(?:of\s+)?'
            r'(RAM|memory|SSD(?:\s+storage)?|storage))?')
COLOR = r'\b(black|white|red|blue|green|yellow|purple|pink|silver|gold|gray|grey|graphite|midnight)\b'
BUDGET = (r'\b(around|about|roughly|up to|at most|no more than|maximum(?: of)?|ceiling(?: is)?)'
          r'\s*(?:USD\s*|\$\s*)(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?![\d.,]\d)')
OPENING = (r"^(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:"
           r"what (?:would|does|is)|how much (?:would|is|for|does)|where can I (?:find|buy)|"
           r"I(?:'m| am) looking for|I(?:'d like| need| want)|compare(?: prices)?(?: for)?|"
           r"price|find(?: me)?|show me|buy)\s+")
SOFT = re.compile(r'\b(?:prefer(?:ably)?|ideally|around|roughly|about|nice to have|would like)\b', re.I)
NEGATIVE = re.compile(r'\b(?:without|except|avoid|not|less than|at most|up to|no more than)\b', re.I)


def interpret(request, wording):
    data = request.model_dump()
    if not wording or request.intent.kind == 'product_link':
        return v2.ShoppingRequest.model_validate(data)
    original = wording.strip()
    text = original
    exact = request.intent.kind == 'exact_model'
    req = data['requirements']
    category = req['category']
    gaps = []
    fulfillment = data['fulfillment']

    def consume(pattern, handler):
        nonlocal text
        text = re.sub(pattern, handler, text, flags=re.I)

    def destination(m):
        city = PUBLIC_CITIES[m[1].casefold()]
        chosen = fulfillment['destination']['city']
        if chosen and city.casefold() != chosen.casefold() or fulfillment['mode'] == 'pickup':
            raise ValueError('The delivery destination in the question conflicts with the fulfillment fields.')
        fulfillment['mode'] = 'delivery'
        fulfillment['destination']['city'] = chosen or city
        return ' '

    consume(DELIVERY, destination)

    def budget(m):
        key = 'target' if m[1].lower() in ('around', 'about', 'roughly') else 'maximum'
        value = v2.Money(currency='USD', amount=m[2].replace(',', ''))
        values = data['budget'] or {}
        if values.get(key) and v2.Money.model_validate(values[key]) != value:
            raise ValueError('The budget in the question conflicts with the budget fields.')
        values[key] = value.model_dump()
        data['budget'] = values
        return ' '

    consume(BUDGET, budget)

    def spec(m, key, value):
        # Compare modifiers within this clause, so "not 8 GB" cannot become a minimum.
        prefix = re.split(r'[.,;!?]|\band\b', m.string[:m.start()], flags=re.I)[-1]
        if SOFT.search(prefix):
            return ' '
        if NEGATIVE.search(prefix) or key not in req:
            gaps.append((prefix + m[0]).strip())
            return ' '
        if req[key] is not None and req[key] != value:
            raise ValueError('The question and specification fields contain different values for ' + key + '.')
        req[key] = value
        return ' '

    def capacity(m):
        unit = (m[3] or '').lower()
        field = 'ram_gb' if unit in ('ram', 'memory') else 'storage_gb'
        minimum = re.search(r'(?:at least|minimum(?: of)?)\s*$', m.string[:m.start()], re.I)
        if minimum or (not exact and 'min_' + field in req):
            field = 'min_' + field
        if unit.startswith('ssd') and category == 'laptop':
            spec(m, 'storage_type', 'SSD')
        return spec(m, field, int(m[1]) * (1024 if m[2].lower() == 'tb' else 1))

    consume(CAPACITY, capacity)
    if category == 'monitor':
        consume(r'\b(\d{2,4})\s*Hz\b', lambda m: spec(m, 'min_refresh_hz', int(m[1])))
        consume(r'\b(\d{3,5})\s*[x×]\s*(\d{3,5})\b',
                lambda m: spec(m, 'min_width_px', int(m[1])) + spec(m, 'min_height_px', int(m[2])))
    elif category == 'tablet':
        consume(r'\b(wi[ -]?fi|cellular)(?: only)?\b',
                lambda m: spec(m, 'connectivity', 'cellular' if m[1].lower() == 'cellular' else 'wifi'))
    elif category == 'audio':
        for pattern, field, value in (
            (r'\b(?:headphones?|headsets?)\b', 'form_factor', 'headphones'),
            (r'\b(?:earbuds?|earphones?)\b', 'form_factor', 'earbuds'),
            (r'\bwired\b', 'connectivity', 'wired'), (r'\bwireless\b', 'connectivity', 'wireless'),
            (r'\b(?:active noise cancell?ation|ANC)\b', 'active_noise_cancellation', True),
        ):
            consume(pattern, lambda m: spec(m, field, value))

    if exact:
        def color(m):
            if SOFT.search(m.string[:m.start()]) or NEGATIVE.search(m.string[:m.start()]):
                gaps.append(m.string[:m.end()].strip())
            elif data['allowed_colors'] and m[1].casefold() not in [c.casefold() for c in data['allowed_colors']]:
                raise ValueError('Specify the allowed colors in one unambiguous selection.')
            else:
                data['allowed_colors'] = (m[1].lower(),)
            return ' '

        consume(COLOR, color)
        text = re.sub(OPENING, '', text.strip(), flags=re.I)
        text = re.sub(r'\b(?:a |the )?(?:new )?pair of\b|^(?:(?:a|an|the|new)\s+)+', ' ', text, flags=re.I)
        text = re.sub(r'^\s*(?:(?:a|an|the|new)\s+)+', '', text, flags=re.I)
        # Remove purchasing boilerplate only when the entire trailing clause matches.
        text = re.sub(r'[.?!]\s*(?:what(?:\'s| is) the total|which seller is cheaper overall|'
                      r'compare sellers if possible|what (?:would|will) (?:it|I) (?:cost|pay))\s*[.?!]*$',
                      '', text, flags=re.I)
        text = re.sub(r'\b(?:cost(?: me)?|in|with|and)\s*[.?!, ]*$', '', text, flags=re.I)
        parts = re.split(r'\s*(?:[;!?]|\.(?:\s|$)|,\s*|\b(?:with|but|must|that has)\b)\s*', text, flags=re.I)
        parts = [p.strip(' .,?!') for p in parts if p.strip(' .,?!')]
        if not parts:
            raise ValueError('Enter the exact model name you want to compare.')
        model, *extra = parts
        gaps.extend(p for p in extra if p.casefold() not in {'new', 'and', 'in', 'and a'})
        data['intent'].update(model=' '.join(model.split()), wording=original)

    data['unresolved_constraints'] = tuple(dict.fromkeys(gaps))
    return v2.ShoppingRequest.model_validate(data)

