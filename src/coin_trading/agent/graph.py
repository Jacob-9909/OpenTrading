from langgraph.graph import END, START, StateGraph
from coin_trading.agent.state import AgentState
from coin_trading.agent.nodes.analyst import technical_analyst_node, sentiment_analyst_node
from coin_trading.agent.nodes.researcher import sequential_debate_node
from coin_trading.agent.nodes.fund_manager import fund_manager_node


def create_trading_agent_graph():
    """Build the trading multi-agent graph.

    Topology:
        START -> [technical_analyst, sentiment_analyst]   (parallel)
              -> sequential_debate (Bull -> Bear)         (sequential by design)
              -> fund_manager
              -> END
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("technical_analyst", technical_analyst_node)
    workflow.add_node("sentiment_analyst", sentiment_analyst_node)
    # Sequential debate: Bear rebuts Bull, so they cannot run in parallel.
    workflow.add_node("sequential_debate", sequential_debate_node)
    workflow.add_node("fund_manager", fund_manager_node)

    workflow.add_edge(START, "technical_analyst")
    workflow.add_edge(START, "sentiment_analyst")

    workflow.add_edge("technical_analyst", "sequential_debate")
    workflow.add_edge("sentiment_analyst", "sequential_debate")

    workflow.add_edge("sequential_debate", "fund_manager")
    workflow.add_edge("fund_manager", END)

    return workflow.compile()
