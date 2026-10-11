# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import pytest
from gatekeeper.recipe import matches_condition, apply_filter

@pytest.mark.parametrize('event,op,target,result', [({},'neq',1,False), ({'x':1},'eq',True,False), ({'x':1},'eq','1',False), ({'x':1},'eq',1,True), ({'x':1},'neq',2,True), ({'x':1},'gt',0,True), ({'x':True},'gt',0,False), ({'x':'2'},'gte',1,False), ({'x':2},'gte',2,True), ({'x':2},'lt',3,True), ({'x':2},'lte',2,True), ({'x':'abcdef'},'contains','cd',True), ({'x':1},'contains','1',False), ({'x':'abc'},'startswith','ab',True), ({'x':1},'in',[True,'1'],False), ({'x':1},'in',[1,2],True), ({'x':1},'not_in',[2],True), ({'x':1},'not_in',None,False)])
def test_filter_semantics(event,op,target,result):
    assert matches_condition(event, {'field':'x','op':op,'value':target}) is result

def test_and_filter():
    assert apply_filter([{'x':1},{'x':2}], [{'field':'x','op':'gt','value':0},{'field':'x','op':'lt','value':2}]) == [{'x':1}]
