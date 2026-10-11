# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from copy import deepcopy
import pytest
from gatekeeper.evaluator import merge_incidents, calculate_metrics, evaluate_recipe, parser_output_valid, aggregation_output_valid, SkillExecutionError


def incident(group,start,end,lines,count=5):
    return {'group':{'src_ip':group},'window_start':start,'window_end':end,'_lines':lines,'count':count}


def test_exact_metrics_neutral_and_null(gk_policy):
    labels={'instances':[{'attack_type':'target','lines':[0,1]}, {'attack_type':'target','lines':[2,3]}, {'attack_type':'other','lines':[4]}]}
    alerts=[incident('a',0,1,[0]),incident('b',0,1,[4]),incident('c',0,1,[8])]
    metrics,cross=calculate_metrics(merge_incidents(alerts),labels,'target',gk_policy.thresholds)
    assert (metrics.true_positives,metrics.false_positives,metrics.false_negatives)==(1,1,1)
    assert metrics.precision == .5 and metrics.recall == .5 and not metrics.passed and cross == 1
    none,cross=calculate_metrics([],labels,'target',gk_policy.thresholds)
    assert none.precision is None and none.recall == 0 and not none.passed
    neutral,cross=calculate_metrics(merge_incidents([alerts[1]]),labels,'target',gk_policy.thresholds)
    assert neutral.precision is None and neutral.false_positives == 0 and cross == 1
    with pytest.raises(ValueError): calculate_metrics([],labels,'missing',gk_policy.thresholds)


def test_merging_touching_windows_and_groups():
    merged=merge_incidents([incident('a',0,10,[0]),incident('a',10,20,[1]),incident('a',19,25,[2]),incident('a',30,40,[3]),incident('b',0,10,[4])])
    assert len(merged)==3
    assert merged[0]['_lines']==[0,1,2] and merged[0]['window_end']==25
    assert merged[1]['_lines']==[3]


def test_threshold_unrounded_boundary(gk_policy):
    labels={'instances':[{'attack_type':'target','lines':[i]} for i in range(10)]}
    metrics,_=calculate_metrics(merge_incidents([incident('a',0,1,list(range(9)))]),labels,'target',gk_policy.thresholds)
    assert metrics.recall == .9 and metrics.passed
    # 0.8996 rounds to 0.900 but must fail a 0.9 threshold.
    labels={'instances':[{'attack_type':'target','lines':[i]} for i in range(2500)]}
    metrics,_=calculate_metrics(merge_incidents([incident('a',0,1,list(range(2249)))]),labels,'target',gk_policy.thresholds)
    assert metrics.recall == .9 and not metrics.passed


class ResultSandbox:
    def __init__(self,results): self.results=iter(results); self.jobs=[]
    async def run(self,code,inputs,params,**kwargs):
        self.jobs.append((code,deepcopy(inputs),params))
        result=next(self.results)
        return result if isinstance(result,dict) and 'status' in result else {'status':'ok','result':result}


def load_skill(gk_manifests,origin='agent'):
    return lambda name: ('unexecuted code',gk_manifests[name],{'origin':origin})


@pytest.mark.asyncio
async def test_execution_filters_feedback_and_no_identity(gk_policy,gk_manifests,gk_recipe):
    events=[{'_line':0,'ts':0.,'src_ip':'203.0.113.1','user':'secret_user','outcome':'failure'}, {'_line':1,'ts':10.,'src_ip':'203.0.113.2','user':'another_secret','outcome':'failure'}, {'_line':2,'ts':20.,'src_ip':'10.0.0.1','user':'normal_secret','outcome':'failure'}, {'_line':3,'ts':30.,'src_ip':'10.0.0.2','user':'success_secret','outcome':'success'}]
    rows=[incident('203.0.113.1',0,1,[0],6),incident('203.0.113.2',0,10,[1],2),incident('10.0.0.1',0,20,[2],6)]
    labels={'instances':[{'attack_type':'ssh_bruteforce','lines':[0]},{'attack_type':'ssh_bruteforce','lines':[1]}]}
    sandbox=ResultSandbox([events,rows])
    result=await evaluate_recipe(gk_recipe,['a','b','c','d'],labels,load_skill(gk_manifests),sandbox,gk_policy,feedback=True)
    assert (result.metrics.true_positives,result.metrics.false_positives,result.metrics.false_negatives)==(1,1,1)
    assert len(result.feedback_examples)==2
    assert all(identity not in str(result.feedback_examples) for identity in ['203.0.113.1','203.0.113.2','10.0.0.1','secret_user','normal_secret'])
    assert len(sandbox.jobs[1][1]) == 3
    result=await evaluate_recipe(gk_recipe,['a','b','c','d'],labels,load_skill(gk_manifests),ResultSandbox([events,rows]),gk_policy)
    assert result.feedback_examples==[]


@pytest.mark.asyncio
async def test_invalid_candidate_vs_seed(gk_policy,gk_manifests,gk_recipe):
    labels={'instances':[{'attack_type':'ssh_bruteforce','lines':[0]}]}
    for answer in [{'status':'timeout','result':None}, ['invalid event'], [{'_line':0,'ts':float('inf')}]]:
        result=await evaluate_recipe(gk_recipe,['a'],labels,load_skill(gk_manifests),ResultSandbox([answer]),gk_policy)
        assert result.metrics.true_positives==0 and result.metrics.false_negatives==1
        with pytest.raises(SkillExecutionError):
            await evaluate_recipe(gk_recipe,['a'],labels,load_skill(gk_manifests,'seed'),ResultSandbox([answer]),gk_policy)


def test_runtime_shapes():
    assert not parser_output_valid([{'_line':True,'ts':0}],1)
    assert not parser_output_valid([{'_line':1,'ts':0}],1)
    assert not aggregation_output_valid([incident('a',0,1,[999])],[{'_line':0}],['count'])
    assert not aggregation_output_valid([incident('a',2,1,[0])],[{'_line':0}],['count'])


@pytest.mark.asyncio
async def test_malicious_group_keys_values_and_window_membership_rejected(gk_policy,gk_manifests,gk_recipe):
    events=[{'_line':0,'ts':0.,'src_ip':'203.0.113.1','user':'attack','outcome':'failure'}, {'_line':1,'ts':10.,'src_ip':'10.0.0.1','user':'normal','outcome':'failure'}]
    labels={'instances':[{'attack_type':'ssh_bruteforce','lines':[0]}]}
    candidates=[{'group':{'secret_username': 'value'},'window_start':0,'window_end':10,'_lines':[1],'count':100},
                {'group':{'src_ip':'10.0.0.1'},'window_start':0,'window_end':1,'_lines':[1],'count':100},
                {'group':{'src_ip':'10.0.0.1'},'window_start':0,'window_end':10,'_lines':[0],'count':100}]
    for malicious in candidates:
        result=await evaluate_recipe(gk_recipe,['a','b'],labels,load_skill(gk_manifests),ResultSandbox([events,[malicious]]),gk_policy,feedback=True)
        assert result.metrics.true_positives==0 and result.metrics.false_positives==0
        assert 'secret_username' not in str(result.feedback_examples)
