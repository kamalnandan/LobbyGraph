// LobbyGraph PoC: tested query templates, one per question.
// Parameters are passed by the app ($group, $group_id). All run in READ access mode.
// Every query returns the provenance of the links it uses, so answers can cite sources.

// Q1. Which entities belong to the group, and which of them are registered lobbyists?
MATCH (g:Group {id: $group_id})<-[p:PART_OF_GROUP]-(e:LegalEntity)
OPTIONAL MATCH (r:LobbyRegistration)-[c:COVERS]->(e)
RETURN e.name AS entity, e.country AS country, e.lei AS lei, p.role AS role,
       collect(DISTINCT r.name) AS registrations,
       p.source AS group_rule, c.source AS covers_source, c.confidence AS covers_confidence
ORDER BY role DESC, entity;

// Q2. How many Commission meetings did the group have in total, across all its entities, and with whom?
MATCH (g:Group {id: $group_id})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)
MATCH (r)-[a:ATTENDED]->(m:Meeting)-[:HELD_BY]->(u:Unit)
WHERE m.date >= coalesce($since, '0000')
WITH DISTINCT m, r, a, u
RETURN count(DISTINCT m) AS meetings,          // each meeting counted once, even if two group entities attended
       collect(DISTINCT coalesce(u.acronym, u.name)) AS units,
       collect(DISTINCT {date: m.date, subject: m.subject, registration: r.name, source: a.source_url}) AS details;

// Q5. Which trade associations does the group belong to, and how big is their lobbying?
//     (Association figures are shown next to the group's own, never added to them.)
MATCH (g:Group {id: $group_id})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)
MATCH (r)-[mo:MEMBER_OF]->(a:LobbyRegistration)
RETURN a.name AS association, a.resolved AS resolved, a.cost_band AS association_cost, a.fte AS association_fte,
       collect(DISTINCT r.name) AS via_registration, mo.confidence AS match_confidence, mo.match_method AS matched_by
ORDER BY resolved DESC, association_fte DESC;

// Q8. Where do sources disagree or data look incomplete for the group?
MATCH (g:Group {id: $group_id})
MATCH (d:DataIssue)-[:CONCERNS]->(x)
WHERE x = g
   OR (x)-[:PART_OF_GROUP]->(g)
   OR (x:LobbyRegistration)-[:COVERS]->(:LegalEntity)-[:PART_OF_GROUP]->(g)
RETURN DISTINCT d.issue_id AS issue, d.type AS type, d.severity AS severity, d.status AS status,
       d.description AS description, d.action AS action
ORDER BY CASE d.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END;

// Q11. Which consultancies does the group hire, and who else hires them?
MATCH (g:Group {id: $group_id})<-[:PART_OF_GROUP]-(:LegalEntity)<-[:COVERS]-(r:LobbyRegistration)
MATCH (r)-[h:HIRED]->(c:LobbyRegistration)
OPTIONAL MATCH (other:LobbyRegistration)-[:HIRED]->(c) WHERE other <> r
RETURN c.name AS consultancy, h.amount_band AS paid_by_group, h.fy_end AS year_end,
       count(DISTINCT other) AS other_clients, collect(DISTINCT other.name)[0..10] AS examples,
       h.source AS source
ORDER BY other_clients DESC;
