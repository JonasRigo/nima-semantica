from nima_semantica.proof_contracts import ProofTarget
from nima_semantica.models import identity
from test_claim_dependencies import ref

def target(**kw):
    fields=dict(target_id="lemma",ref=ref("a"),statement="For every real x, (x+1)^2=x^2+2*x+1.",
        argument="",domain="reals",quantifier="forall",assumptions=(),dependencies=(),unresolved_obligations=("Check polynomial expansion.",),
        source_region_ids=(),granularity="lemma",definitions=())
    fields.update(kw)
    return ProofTarget(**fields,content_revision=identity(fields))

