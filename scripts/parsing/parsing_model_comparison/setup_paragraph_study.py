from pathlib import Path
BASE=Path(__file__).resolve().parent
root=BASE/'paragraph_quality_study'
root.mkdir(exist_ok=True)
script=(BASE/'chunk_size_study/run_study.py').read_text(encoding='utf-8')
script=script.replace('SOLVE_Parsing_Fein_Batch_1.1.0','SOLVE_Parsing_Fein_Batch_1.2.0')
script=script.replace('from prepare_document import make_chunks','from prepare_document import make_chunks, prepare_document, normalize_quote')
script=script.replace("    prepared = read_json(ROOT.parent / 'repeated_self_relations/prepared_all.json')", """    previous = read_json(ROOT.parent / 'repeated_self_relations/prepared_all.json')
    prepared = prepare_document(Path(previous['document']['local_file']), ROOT/'prepared', '10,11,17', 4000)
    prepared['blocks'] = [b for b in prepared['blocks'] if b['page'] in {10,11,17}]
    old_text = normalize_quote('\\n'.join(b['text'] for b in previous['blocks']))
    new_text = normalize_quote('\\n'.join(b['text'] for b in prepared['blocks']))
    assert old_text == new_text, 'Source text changed beyond paragraph boundaries'
    write_json(ROOT/'source_equivalence.json', {'normalized_text_identical':True, 'old_blocks':len(previous['blocks']), 'new_blocks':len(prepared['blocks'])})
    write_json(ROOT/'prepared_all.json', prepared)""")
script=script.replace('sizes = [2000, 4000, 10000]', 'sizes = [4000]')
script=script.replace('Fixed pre-extracted blocks and order; only make_chunks target changes.', 'Parser 1.2.0, fresh paragraph-preserving preparation; normalized source text verified identical to prior study. Soft 4000-character target. Comparison changes preprocessing, prompt, quantity schema and quality repair together.')
script=script.replace('from schemas import Event, Relationship, Extraction, read_headers','from schemas import Event, Relationship, Extraction, read_headers, extraction_schema')
script=script.replace('digest(Extraction.model_json_schema())','digest(extraction_schema(ontology))')
target=root/'run_study.py'
assert not target.exists()
target.write_text(script,encoding='utf-8')
print(target)
