from langgraph.graph import END, START, StateGraph
from coin_trading.agent.state import AgentState
from coin_trading.agent.nodes.analyst import technical_analyst_node, sentiment_analyst_node
from coin_trading.agent.nodes.researcher import bull_researcher_node, bear_researcher_node
from coin_trading.agent.nodes.fund_manager import fund_manager_node


def create_trading_agent_graph():
    """Build the trading multi-agent graph.

    Topology:
        START -> [technical_analyst, sentiment_analyst]        (parallel)
              -> [bull_researcher, bear_researcher]            (parallel, independent)
              -> fund_manager
              -> END
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("technical_analyst", technical_analyst_node)
    workflow.add_node("sentiment_analyst", sentiment_analyst_node)
    workflow.add_node("bull_researcher", bull_researcher_node)
    workflow.add_node("bear_researcher", bear_researcher_node)
    workflow.add_node("fund_manager", fund_manager_node)

    workflow.add_edge(START, "technical_analyst")
    workflow.add_edge(START, "sentiment_analyst")

    workflow.add_edge("technical_analyst", "bull_researcher")
    workflow.add_edge("technical_analyst", "bear_researcher")
    workflow.add_edge("sentiment_analyst", "bull_researcher")
    workflow.add_edge("sentiment_analyst", "bear_researcher")

    workflow.add_edge("bull_researcher", "fund_manager")
    workflow.add_edge("bear_researcher", "fund_manager")
    workflow.add_edge("fund_manager", END)

    return workflow.compile()
