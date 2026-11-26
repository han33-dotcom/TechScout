"""Thin same-origin JSON handlers over the durable buyer application service."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response, Query
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator

from app.buyer_interpretation import interpret
from app.buyer_runs import RunError
from app.contracts import Model, Text
from app.contracts_v2 import Country, MarketScope, ShoppingRequest, RunSnapshot, RunReference, ComparisonWorkspace
from app.buyer_history import HistoryList, HistoricalWorkspace, DeletionResult, export_comparison
from app.buyer_accounts import AccountSummary, DestinationState, LocationList, SavedLocation

COOKIE = 'techscout_session'


class SessionInfo(Model):
    csrf_token: Text
    expires_at: datetime
    # Additive since #187: the signed-in TechScout account, if any.
    account: AccountSummary | None = None


# Account and location bodies use plain strings: the service validates them and its
# errors never echo a submitted password or address back to the browser.
class Credentials(Model):
    username: str
    password: str


class LocationDraft(Model):
    label: str
    city: str
    street: str
    details: str | None = None


class DestinationChoice(Model):
    location_id: str
    recipients: list[str]


class RunCommand(Model):
    run: RunReference


class ClarificationCommand(RunCommand):
    clarification_id: Text
    answer: str = Field(min_length=1,max_length=4000)


class DomesticMarketScope(MarketScope):
    """Accepted on writes. Saved v2 requests retain their original market scope."""
    include_international: Literal[False] = False
    international_markets: tuple[Country, ...] = Field(default=(), max_length=0)

    @field_validator('include_international', mode='before')
    @classmethod
    def domestic_only(cls, value):
        if value is not False:
            raise ValueError('TechScout searches domestic offers in Lebanon only')
        return value


class DomesticShoppingRequest(ShoppingRequest):
    buyer_text: str | None = Field(default=None, max_length=4000, exclude=True)

    def interpreted(self):
        try:
            return interpret(self, self.buyer_text)
        except ValueError as exc:
            raise RunError(422, str(exc)) from exc

    market_scope: DomesticMarketScope = Field(default_factory=DomesticMarketScope)


class RefinementCommand(RunCommand):
    request: DomesticShoppingRequest


class BodyLimit:
    """Bound chunked and Content-Length request bodies before JSON parsing."""
    def __init__(self,app,max_bytes=65536):
        self.app,self.max_bytes=app,max_bytes

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in {'POST','PUT','PATCH'}:
            return await self.app(scope,receive,send)
        chunks,total=[],0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':
                return
            chunk=message.get('body',b'')
            total+=len(chunk)
            if total>self.max_bytes:
                return await JSONResponse({'detail':'Request body is too large'},status_code=413)(scope,receive,send)
            chunks.append(chunk)
            if not message.get('more_body',False):
                break
        delivered=False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        await self.app(scope,bounded_receive,send)


def router(*,cookie_secure=True):
    api=APIRouter(prefix='/api')

    def owned(request,*,mutate=False):
        token=request.cookies.get(COOKIE)
        if mutate:
            origin=request.headers.get('origin')
            # Vite preserves the incoming Host; the browser still sees one origin.
            # Production TLS proxies must forward scheme/host through trusted hops.
            expected=str(request.base_url).rstrip('/')
            if origin!=expected or request.headers.get('sec-fetch-site')=='cross-site':
                raise RunError(403,'Mutations require the same origin')
            request.app.state.buyer_runs.authorize(token,request.headers.get('x-csrf-token'))
        return token

    def issued(response,token,csrf,expires,account=None):
        response.set_cookie(COOKIE,token,httponly=True,secure=cookie_secure,samesite='strict',
                            path='/',expires=expires)
        response.headers['Cache-Control']='no-store'
        return SessionInfo(csrf_token=csrf,expires_at=expires,account=account)

    @api.get('/session',response_model=SessionInfo)
    def session(request:Request,response:Response):
        if request.headers.get('sec-fetch-site')=='cross-site':
            raise RunError(403,'Session creation requires the same origin')
        return issued(response,*request.app.state.buyer_accounts.session(request.cookies.get(COOKIE)))

    @api.post('/account/register',response_model=SessionInfo,status_code=201)
    def register(body:Credentials,request:Request,response:Response):
        token=owned(request,mutate=True)
        return issued(response,*request.app.state.buyer_accounts.register(token,body.username,body.password))

    @api.post('/account/sign-in',response_model=SessionInfo)
    def sign_in(body:Credentials,request:Request,response:Response):
        token=owned(request,mutate=True)
        return issued(response,*request.app.state.buyer_accounts.sign_in(token,body.username,body.password))

    @api.post('/account/sign-out',response_model=SessionInfo)
    def sign_out(request:Request,response:Response):
        return issued(response,*request.app.state.buyer_accounts.sign_out(owned(request,mutate=True)))

    @api.get('/account/locations',response_model=LocationList)
    def locations(request:Request):
        return request.app.state.buyer_accounts.locations(owned(request))

    @api.post('/account/locations',response_model=SavedLocation,status_code=201)
    def add_location(body:LocationDraft,request:Request):
        return request.app.state.buyer_accounts.add_location(owned(request,mutate=True),**body.model_dump())

    @api.post('/account/locations/{location_id}/delete',response_model=DeletionResult)
    def delete_location(location_id:str,request:Request):
        request.app.state.buyer_accounts.delete_location(owned(request,mutate=True),location_id)
        return DeletionResult()

    @api.get('/runs/{run_id}/destination',response_model=DestinationState)
    def destination(run_id:str,request:Request):
        return request.app.state.buyer_accounts.destination(owned(request),run_id)

    @api.post('/runs/{run_id}/destination',response_model=DestinationState)
    def select_destination(run_id:str,body:DestinationChoice,request:Request):
        return request.app.state.buyer_accounts.select_destination(owned(request,mutate=True),run_id,
                                                                    body.location_id,body.recipients)

    @api.post('/runs/{run_id}/destination/decline',response_model=DestinationState)
    def decline_destination(run_id:str,request:Request):
        return request.app.state.buyer_accounts.decline_destination(owned(request,mutate=True),run_id)

    @api.post('/runs',response_model=RunSnapshot,status_code=202)
    def start(body:DomesticShoppingRequest,request:Request):
        return request.app.state.buyer_runs.start(owned(request,mutate=True),body.interpreted())

    @api.get('/runs/{run_id}',response_model=ComparisonWorkspace)
    def read(run_id:str,request:Request):
        return request.app.state.buyer_runs.read(owned(request),run_id)

    @api.get('/history',response_model=HistoryList)
    def history(request:Request,limit:int=Query(default=20,ge=1,le=20)):
        return request.app.state.buyer_runs.history(owned(request),limit)

    @api.get('/history/{run_id}',response_model=HistoricalWorkspace)
    def reopen(run_id:str,request:Request):
        return request.app.state.buyer_runs.reopen(owned(request),run_id)

    @api.post('/history/{run_id}/delete',response_model=DeletionResult)
    def delete_history(run_id:str,request:Request):
        return request.app.state.buyer_runs.delete_history(owned(request,mutate=True),run_id)

    @api.get('/history/{run_id}/export')
    def export(run_id:str,request:Request,include_evidence:bool=True):
        saved=request.app.state.buyer_runs.reopen(owned(request),run_id)
        return Response(export_comparison(saved.workspace.current,include_evidence=include_evidence),
            media_type='text/plain',headers={'Content-Disposition':'attachment; filename="techscout-comparison.txt"',
                'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @api.post('/runs/{run_id}/cancel',response_model=RunSnapshot)
    def cancel(run_id:str,body:RunCommand,request:Request):
        token=owned(request,mutate=True)
        if run_id!=body.run.run_id:
            raise RunError(409,'URL and run reference differ')
        return request.app.state.buyer_runs.cancel(token,body.run)

    @api.post('/runs/{run_id}/clarify',response_model=RunSnapshot,status_code=202)
    def clarify(run_id:str,body:ClarificationCommand,request:Request):
        token=owned(request,mutate=True)
        if run_id!=body.run.run_id:
            raise RunError(409,'URL and run reference differ')
        return request.app.state.buyer_runs.clarify(token,body.run,body.clarification_id,body.answer)

    @api.post('/runs/{run_id}/refine',response_model=ComparisonWorkspace,status_code=202)
    def refine(run_id:str,body:RefinementCommand,request:Request):
        token=owned(request,mutate=True)
        if run_id!=body.run.run_id:
            raise RunError(409,'URL and run reference differ')
        return request.app.state.buyer_runs.refine(token,body.run,body.request.interpreted())

    return api
