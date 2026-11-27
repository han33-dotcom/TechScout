"""Inputs to shared evidence storage after an approved public fetch."""
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StrictBytes, TypeAdapter, ValidationError, model_validator

from app.collection_contracts import CorpusVersion
from app.contracts import Fact, HttpsUrl, Model, Money, Text, UtcTime
from app.contracts_v2 import (
    AudioSpecs, Country, EstimatedCost, Evidence, LaptopSpecs, MonitorSpecs, Offer, Origin, PhoneSpecs, TabletSpecs, Variant,
)
from app.sources import MAX_RESPONSE_BYTES

RawBody = Annotated[StrictBytes, Field(min_length=1, max_length=MAX_RESPONSE_BYTES)]


class ResourceKey(Model):
    source_id: Text
    url: HttpsUrl
    representation: Text
    product_id: Text | None = None
    variant_id: Text | None = None
    branch_id: Text | None = None
    route_id: Text | None = None

    @model_validator(mode='after')
    def scoped_resource(self):
        if self.url.fragment or (self.variant_id is not None and self.product_id is None):
            raise ValueError('Resource keys require a fragment-free URL and a product for a variant')
        return self


class ProcessingVersion(CorpusVersion):
    extraction_version: Text
    chunk_version: Text


class DocumentCapture(Model):
    key: ResourceKey
    final_url: HttpsUrl
    visibility: Literal['public', 'private']
    origin: Origin
    checked_at: UtcTime
    observed_at: UtcTime
    body: RawBody
    relevant_content: RawBody
    processing: ProcessingVersion
    etag: Text | None = None
    last_modified: Text | None = None

    @model_validator(mode='after')
    def captured_response(self):
        if self.observed_at > self.checked_at or self.final_url.fragment:
            raise ValueError('A capture needs a fragment-free final URL and an observation by check time')
        return self


def binding(receipt: Evidence):
    return (receipt.scope, receipt.product_id, receipt.variant_id, receipt.branch_id,
            receipt.route_id, receipt.field_path)


# Use the offer/specification contract's field validators for reusable, structured facts.
_FACT_TYPES = {
    field: [TypeAdapter(Offer.model_fields[field].annotation)]
    for field in ('price', 'stock', 'purchasable', 'condition', 'activation', 'payment', 'arrival')
}
_FACT_TYPES['variant.color'] = [TypeAdapter(Variant.model_fields['color'].annotation)]
for field in ('manufacturer', 'model'):
    _FACT_TYPES['variant.product.' + field] = [TypeAdapter(Fact[Text])]
for model in (PhoneSpecs, LaptopSpecs, TabletSpecs, MonitorSpecs, AudioSpecs):
    for field, definition in model.model_fields.items():
        if field != 'category':
            _FACT_TYPES.setdefault('variant.specs.' + field, []).append(TypeAdapter(definition.annotation))
_POLICY_TEXT = TypeAdapter(Fact[Text])
for field in ('catalog.gtin', 'catalog.mpn', 'catalog.configuration_id', 'catalog.delivery_city', 'seller.name'):
    _FACT_TYPES[field] = [_POLICY_TEXT]
for field in ('catalog.delivery_country', 'route.origin_country', 'seller.market'):
    _FACT_TYPES[field] = [TypeAdapter(Fact[Country])]
_FACT_TYPES['costs.order.tax'] = [TypeAdapter(Fact[Literal['included']])]
_FACT_TYPES['route.item_eligible'] = [TypeAdapter(Fact[bool])]
_FACT_TYPES['resource.availability'] = [TypeAdapter(Fact[Literal['discontinued']])]
_FACT_TYPES['costs.order.shipping'] = [TypeAdapter(Fact[Money])]
_FACT_TYPES['costs.order.shipping_range'] = [TypeAdapter(Fact[EstimatedCost])]


class FieldObservation(Model):
    fact: Fact[JsonValue]
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]

    @model_validator(mode='after')
    def supported_fact(self):
        first = self.evidence[0]
        ids = {item.evidence_id for item in self.evidence}
        if (ids != set(self.fact.evidence_ids) or len(ids) != len(self.evidence)
                or any(binding(item) != binding(first) for item in self.evidence)):
            raise ValueError('Each fact needs receipts for its own uniquely scoped field')

        scopes = {'price': {'variant'}, 'stock': {'variant', 'branch'}, 'purchasable': {'variant'},
                  'resource.availability': {'variant'}}
        if first.field_path in scopes and (
            first.scope not in scopes[first.field_path] or not first.variant_id or not first.product_id
        ):
            raise ValueError('Price, stock and purchasability require the observed configuration')

        if first.field_path.startswith(('catalog.', 'route.', 'costs.order.')) and not (
            first.product_id and first.variant_id and first.scope ==
            ('route' if first.field_path.startswith(('route.', 'costs.order.shipping')) else 'variant')
        ):
            raise ValueError('Catalog facts require a selected item and its applicable route')
        validators = _FACT_TYPES.get(first.field_path, ())
        if first.field_path.startswith('policy.') and first.scope == 'source':
            validators = (_POLICY_TEXT,)
        data = self.fact.model_dump(mode='json')
        for validator in validators:
            try:
                normalized = validator.validate_python(data)
            except ValidationError:
                continue
            object.__setattr__(self, 'fact', Fact[JsonValue].model_validate(normalized.model_dump(mode='json')))
            return self
        raise ValueError('Invalid structured fact for the observed field')


class PassageDraft(Model):
    locator: Text
    text: Text
    evidence: Evidence


class Extraction(Model):
    observations: tuple[FieldObservation, ...] = ()
    passages: tuple[PassageDraft, ...] = ()

    @model_validator(mode='after')
    def distinct_fields_and_passages(self):
        fields = {binding(item.evidence[0]) for item in self.observations}
        passages = {(binding(item.evidence), item.locator) for item in self.passages}
        if len(fields) != len(self.observations) or len(passages) != len(self.passages):
            raise ValueError('Report conflicting values in one fact and identify each chunk uniquely')
        return self
