install:
	pip install -r requirements.txt

db:
	python -m scripts.migrate
	python -m scripts.seed_client config/clients/byosync.yaml

collect:
	python -m collectors.ats_jobs
	python -m collectors.cert_transparency
	python -m collectors.github_meta
	python -m collectors.news_rss
	python -m collectors.launch_feeds
	python -m core.fit

rank:
	python -m core.score

queue:
	python -m outreach.draft

report:
	python -m outreach.ledger

test:
	python -m pytest -q
