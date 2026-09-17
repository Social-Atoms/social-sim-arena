"""Execute the trusted workflow script with GitHub metadata fixtures."""
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_binding_suggestion():
    workflow=(ROOT/'.github/workflows/registration-binding.yml').read_text()
    assert 'actions/checkout' not in workflow
    assert 'contents: read' in workflow
    script=workflow.split('          script: |\n',1)[1]
    script='\n'.join(line[12:] for line in script.splitlines())
    harness=r'''
const outputs=[];
const context={repo:{owner:'arena',repo:'test'},payload:{pull_request:{number:1,user:{login:'XuanL17',type:'User'},head:{sha:'head'}}}};
const lines=JSON.stringify({entrant_id:'sample',github:'',keys:[]},null,2).split('\n');
const files=[{status:'added',filename:'entrants/sample.json',patch:'@@ -0,0 +1,7 @@\n'+lines.map(l=>'+'+l).join('\n')}];
const github={rest:{pulls:{listFiles:'files',listReviewComments:'comments',createReviewComment:async c=>outputs.push(c)}},paginate:async what=>what==='files'?files:[]};
async function run(){
SCRIPT
}
(async()=>{await run();const first=outputs.slice();outputs.length=0;files[0].status='modified';await run();process.stdout.write(JSON.stringify({first,modified:outputs}));})();
'''.replace('SCRIPT',script)
    result=json.loads(subprocess.check_output(['node','-e',harness],text=True))
    assert len(result['first'])==1
    comment=result['first'][0]
    assert '"github": "XuanL17"' in comment['body']
    assert 'Commit suggestion' in comment['body']
    assert comment['commit_id']=='head'
    assert comment['path']=='entrants/sample.json'
    assert result['modified']==[]

if __name__=='__main__':
    test_binding_suggestion()
    print('registration binding tests passed')
