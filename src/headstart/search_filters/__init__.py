"""The Search-filter vocabulary (ADR-0193, ADR-0232): each materialized filter's column, verdict and
clause, and the compiler that turns a request's filters into a LanceDB where-clause. The run imports
it to write the columns, so it sits beside `search`, never under it.
"""
